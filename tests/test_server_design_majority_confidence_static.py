from pathlib import Path


PLAN = Path("stoney_verify/services/server_design_plan_service.py").read_text()
CONFIDENCE = Path("stoney_verify/services/server_design_repair_confidence.py").read_text()
V2 = Path("stoney_verify/commands_ext/public_design_studio_v2.py").read_text()


def test_smart_auto_detect_uses_native_confidence_engine() -> None:
    for phrase in (
        "server_design_repair_confidence",
        "evaluate_repair_plan",
        'context="smart_category_auto_detect"',
        "__repair_confidence_result",
        "_fail_closed_on_low_confidence",
    ):
        assert phrase in PLAN

    for phrase in (
        "confidence_summary_text",
        "BLOCKED_AESTHETIC_DOWNGRADE",
        "BLOCKED_LOW_CONFIDENCE",
        "apply_allowed",
    ):
        assert phrase in CONFIDENCE


def test_smart_auto_detect_is_category_aware_and_saved_rules_remain_authoritative() -> None:
    assert "build_category_aware_options" in PLAN
    assert "annotate_category_aware_plan_items" in PLAN
    assert "respect_saved_rules=True" in PLAN
    assert "mixed categories" in PLAN.lower()
    assert "Saved narrow rules win" in V2


def test_low_confidence_plan_is_non_applicable_before_the_ui_renders() -> None:
    assert 'item["status"] = "failed"' in PLAN
    assert "confidence is too low" in PLAN
    assert "This preview has blockers" in V2
    assert "This preview is obsolete" in V2
