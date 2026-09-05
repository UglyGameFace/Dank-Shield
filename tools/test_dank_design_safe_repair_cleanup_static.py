from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
PLAN = (ROOT / "stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")
ENHANCEMENTS = (ROOT / "stoney_verify/commands_ext/public_design_enhancements.py").read_text(encoding="utf-8")
STARTUP = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")


def test_native_design_owns_enhancements_without_runtime_patch_loading() -> None:
    assert "server_design_plan_service as plans" in V2
    assert "server_design_majority_layout_guard" not in ENHANCEMENTS
    assert "server_design_strict_layout_guard" not in ENHANCEMENTS
    assert "server_design_majority_layout_guard" not in STARTUP
    assert "server_design_strict_layout_guard" not in STARTUP


def test_fix_mismatched_names_copy_mentions_saved_rules_win() -> None:
    assert "Narrow saved rules always win" in V2
    assert "keeps saved narrow rules authoritative" in V2
    assert "respect_saved_rules=True" in PLAN
    assert "build_category_aware_options" in PLAN
    assert "copies the live server style" not in V2
    assert "ignores saved rules" not in V2


def test_native_plan_preserves_category_aware_auto_detect_and_strict_matching() -> None:
    assert "majority.build_category_aware_options(studio, plan_options, records)" in PLAN
    assert "majority.annotate_category_aware_plan_items(studio, items, plan_options)" in PLAN
    assert 'out["exact_match"] = True' in PLAN
    assert "_strict_lock_map" in PLAN


def test_smart_auto_detect_apply_fails_closed_on_low_confidence() -> None:
    assert "repair_confidence.evaluate_repair_plan" in PLAN
    assert 'context="smart_category_auto_detect"' in PLAN
    assert "_fail_closed_on_low_confidence" in PLAN
    assert 'item["status"] = "failed"' in PLAN
    assert "confidence is too low" in PLAN
    assert "This preview is obsolete" in V2


def test_active_public_flow_has_one_explicit_legacy_compatibility_boundary() -> None:
    assert "command_guard.build_design_plan =" not in PLAN
    assert "DesignDoctorView =" not in PLAN
    assert "activate_public_design_enhancements" not in (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
    assert "def _install_legacy_compatibility_bridge" in V2
    bridge = V2[V2.index("def _install_legacy_compatibility_bridge"):V2.index("\n\n_install_legacy_compatibility_bridge()")]
    assert "legacy.build_design_plan =" not in bridge
    assert "legacy.DesignDoctorView =" not in bridge
    assert "legacy._home_embed = _home_embed" in bridge
    assert "legacy.DesignPreviewView = ReviewedPreviewView" in bridge
    assert "async def build_design_plan" in LEGACY


if __name__ == "__main__":
    for test in (
        test_native_design_owns_enhancements_without_runtime_patch_loading,
        test_fix_mismatched_names_copy_mentions_saved_rules_win,
        test_native_plan_preserves_category_aware_auto_detect_and_strict_matching,
        test_smart_auto_detect_apply_fails_closed_on_low_confidence,
        test_active_public_flow_has_one_explicit_legacy_compatibility_boundary,
    ):
        test()
        print(f"PASS {test.__name__}")
