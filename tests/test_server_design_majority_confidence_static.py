from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_majority_layout_guard.py").read_text()


def test_majority_repair_uses_confidence_engine():
    required = [
        "server_design_repair_confidence",
        "evaluate_repair_plan",
        "confidence_summary_text",
        "Repair confidence",
        "Blocked by design safety",
        "Needs review",
        "Apply disabled when confidence is low",
    ]

    for phrase in required:
        assert phrase in SOURCE


def test_majority_repair_no_longer_recommends_live_majority_blindly():
    assert "For hand-built servers, choose **Use Live Majority**." not in SOURCE
    assert "Fix Only Obvious Mistakes" in SOURCE
    assert "Use **Live Majority** only when the preview keeps the current server look." in SOURCE


def test_majority_apply_is_blocked_by_confidence_without_rewriting_internal_ids():
    assert "__repair_confidence_result" in SOURCE
    assert "Repair confidence blocked automatic apply" in SOURCE
    assert "repair_confidence_blocked" in SOURCE
