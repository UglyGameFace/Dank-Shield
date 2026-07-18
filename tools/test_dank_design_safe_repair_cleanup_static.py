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
    assert "Saved rules / locks" in PUBLIC
    assert "live detection is preview-only when saved rules exist" in PUBLIC
    assert "compares names against saved category/channel rules" in PUBLIC
    assert "copies the live server style" not in PUBLIC
    assert "ignores saved rules" not in PUBLIC


def test_native_majority_context_respects_locks() -> None:
    assert "respect_locks=True" in PUBLIC
    assert "repair_options = majority.apply_majority_to_options(studio, options, analysis, respect_locks=True)" in PUBLIC


def test_smart_auto_detect_apply_requires_high_confidence_but_keeps_saved_rules() -> None:
    assert "Preview Smart Auto-Detect" in MAJORITY_GUARD
    assert "live_apply_allowed" in MAJORITY_GUARD
    assert 'bool(confidence.get("apply_allowed"))' in MAJORITY_GUARD
    assert "saved_rules == 0" not in MAJORITY_GUARD
    assert "DesignPreviewView(can_apply=live_apply_allowed)" in MAJORITY_GUARD
    assert "__auto_detect_saved_rules_respected_count" in MAJORITY_GUARD


def test_smart_auto_detect_plan_is_category_aware_and_saved_locks_win() -> None:
    assert "majority.build_category_aware_options(studio, options, records)" in MAJORITY_GUARD
    assert "majority.annotate_category_aware_plan_items(studio, items, inferred)" in MAJORITY_GUARD
    assert "saved channel/category/global rules always win" in MAJORITY_GUARD.lower()
    assert "respect_saved_locks = bool(_saved_rule_count(options))" not in MAJORITY_GUARD


if __name__ == "__main__":
    for test in (
        test_native_design_owns_enhancements_not_startup_loading,
        test_fix_mismatched_names_copy_mentions_saved_rules_win,
        test_native_majority_context_respects_locks,
        test_smart_auto_detect_apply_requires_high_confidence_but_keeps_saved_rules,
        test_smart_auto_detect_plan_is_category_aware_and_saved_locks_win,
    ):
        test()
        print(f"PASS {test.__name__}")
