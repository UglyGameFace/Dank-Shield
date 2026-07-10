from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")


def test_home_has_clear_single_path_wording() -> None:
    assert "Start here — pick one path" in PUBLIC
    assert "Preview Saved Design" in PUBLIC
    assert "Repair Saved Design" in PUBLIC
    assert "Change One Style" in PUBLIC
    assert "Fix Mismatched Names" not in PUBLIC
    assert "Find & Fix Inconsistencies" not in PUBLIC


def test_lock_unlock_section_is_on_home() -> None:
    assert "Lock / unlock" in PUBLIC
    assert "Lock / Unlock Items" in PUBLIC
    assert "LockUnlockButton" in PUBLIC
    assert "self.add_item(LockUnlockButton(row=4))" in PUBLIC


def test_lock_unlock_home_view_exposes_both_lock_types() -> None:
    assert "Lock / Unlock Categories & Channels" in PUBLIC
    assert "class LockUnlockHomeView" in PUBLIC
    assert "Manage Saved Style Locks" in PUBLIC
    assert "Protected Names / Unlock" in PUBLIC
    assert "clear one lock, clear all locks" in PUBLIC


def test_style_change_missing_icons_is_clear_and_not_dead_end() -> None:
    assert "Optional/manual choices" in PUBLIC
    assert "choose icons in batches" in PUBLIC
    assert "open Channel Editor and fix individually" not in PUBLIC
    assert "Too many missing-emoji rows for one modal" not in PUBLIC


def test_old_conflicting_design_copy_is_gone() -> None:
    for forbidden in (
        "Review Repairs",
        "rename protection",
        "should never be renamed",
    ):
        assert forbidden not in PUBLIC, f"old/confusing design wording still present: {forbidden}"


if __name__ == "__main__":
    for test in (
        test_home_has_clear_single_path_wording,
        test_lock_unlock_section_is_on_home,
        test_lock_unlock_home_view_exposes_both_lock_types,
        test_style_change_missing_icons_is_clear_and_not_dead_end,
        test_old_conflicting_design_copy_is_gone,
    ):
        test()
        print(f"PASS {test.__name__}")
