from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def _block(name: str) -> str:
    start = SOURCE.find(name)
    assert start != -1, f"{name} not found"
    end = len(SOURCE)
    for marker in ("\nclass ", "\ndef ", "\nasync def "):
        pos = SOURCE.find(marker, start + 1)
        if pos != -1:
            end = min(end, pos)
    return SOURCE[start:end]


def test_home_embed_shows_detected_live_style_not_saved_as_current():
    block = _block("def _home_embed")
    assert "Detected live style" in block
    assert "Saved design rule" in block
    assert "Review Repairs ignores these unless you choose saved layout." in block
    assert "Current style" not in block


def test_review_repairs_uses_native_live_majority_context():
    block = _block("async def consistency_check")
    assert "_infer_live_majority_context(guild, options)" in block
    assert "majority.annotate_plan_items(items, analysis, repair_options, studio=studio)" in block
    assert '"__use_live_majority_layout"] = True' in block  # fallback only


def test_consistency_embed_labels_live_majority_truthfully():
    block = _block("def _consistency_embed")
    assert "Live Majority Repair Preview" in block
    assert "Detected target layout" in block
    assert "Saved rules ignored for this repair" in block
    assert "saved design draft" not in block.lower()


def test_live_majority_helpers_exist():
    assert "def _live_majority_records_for_design" in SOURCE
    assert "def _infer_live_majority_context" in SOURCE
    assert "majority.infer_live_majority_layout(studio, records)" in SOURCE
    assert "majority.apply_majority_to_options(studio, options, analysis, respect_locks=False)" in SOURCE
