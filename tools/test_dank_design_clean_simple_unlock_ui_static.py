from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")


def test_home_has_clear_current_workflow() -> None:
    assert "Pick **one job** below" in V2
    assert "Design Entire Server" in V2
    assert "Edit One Category / Channel" in V2
    assert "Fix Inconsistent Names" in V2
    assert "Saved Rules & Protection" in V2
    assert "Undo Last Apply" in V2
    assert "Fix Mismatched Names" not in V2
    assert "Find & Fix Inconsistencies" not in V2


def test_rules_and_unlocks_is_reachable() -> None:
    assert 'label="Saved Rules & Protection"' in V2
    assert 'label="Layout Rules"' in V2
    assert 'label="Remove One Rule"' in V2
    assert 'label="Protection"' in V2
    assert "class LockManagerView" in LEGACY
    assert "class ProtectionManagerView" in LEGACY


def test_lock_manager_covers_saved_rule_scopes() -> None:
    assert "category_format_locks" in LEGACY
    assert "channel_format_locks" in LEGACY
    assert "class LockRemoveButton" in LEGACY
    assert "Clean Stale" in LEGACY
    assert "Remove this one rule only" in LEGACY or "remove exactly one" in V2


def test_style_change_missing_icons_batches_without_dead_end() -> None:
    assert "Choose Missing Icons" in LEGACY
    assert "batches of 5" in LEGACY
    assert "batch = missing[:5]" in LEGACY
    assert "StyleChangeFixMissingEmojiButton" in LEGACY
    assert "Too many missing-emoji rows for one modal" not in LEGACY


def test_current_design_contract_keeps_preview_first_safety() -> None:
    assert "Preview Server Changes" in V2
    assert "Apply Reviewed Changes" in V2
    assert "Nothing is renamed" in V2
    assert "Undo Last Apply" in V2


if __name__ == "__main__":
    for test in (
        test_home_has_clear_current_workflow,
        test_rules_and_unlocks_is_reachable,
        test_lock_manager_covers_saved_rule_scopes,
        test_style_change_missing_icons_batches_without_dead_end,
        test_current_design_contract_keeps_preview_first_safety,
    ):
        test()
        print(f"PASS {test.__name__}")