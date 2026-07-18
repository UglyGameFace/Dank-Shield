from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = (
    ROOT
    / "stoney_verify/commands_ext/public_design_studio.py"
).read_text(encoding="utf-8")


def test_home_has_clear_current_workflow() -> None:
    assert "Safe workflow: review first" in PUBLIC
    assert "Recommended workflow" in PUBLIC
    assert "Preview Saved Design" in PUBLIC
    assert "Review Name Drift" in PUBLIC
    assert "Change Channel Separator Only" in PUBLIC
    assert "Category Editor" in PUBLIC
    assert "Channel Editor" in PUBLIC
    assert "Fix Mismatched Names" not in PUBLIC
    assert "Find & Fix Inconsistencies" not in PUBLIC


def test_rules_and_unlocks_is_reachable() -> None:
    assert 'label="Rules & Unlocks"' in PUBLIC
    assert "Unlock Saved Rules" in PUBLIC
    assert "Protected Names / Unlock" in PUBLIC
    assert "class LockManagerButton" in PUBLIC
    assert "class LockManagerView" in PUBLIC


def test_lock_manager_covers_saved_rule_scopes() -> None:
    assert "category_format_locks" in PUBLIC
    assert "channel_format_locks" in PUBLIC
    assert "class LockRemoveButton" in PUBLIC
    assert "Clean Stale" in PUBLIC
    assert "remove individual overrides" in PUBLIC


def test_style_change_missing_icons_batches_without_dead_end() -> None:
    assert "Choose Missing Icons" in PUBLIC
    assert "batches of 5" in PUBLIC
    assert "batch = missing[:5]" in PUBLIC
    assert "StyleChangeFixMissingEmojiButton" in PUBLIC
    assert "Too many missing-emoji rows for one modal" not in PUBLIC


def test_current_design_contract_keeps_preview_first_safety() -> None:
    assert "Preview Saved Design" in PUBLIC
    assert "Apply Reviewed Changes" in PUBLIC
    assert "Nothing has been changed yet" in PUBLIC
    assert "Rollback" in PUBLIC


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
