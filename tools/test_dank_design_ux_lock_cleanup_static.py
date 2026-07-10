from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
SAFE_TEST = (ROOT / "tools/test_dank_design_safe_repair_cleanup_static.py").read_text(encoding="utf-8")


def test_home_uses_clear_workflows_not_old_vague_labels() -> None:
    assert "Recommended workflow" in PUBLIC
    assert "Preview Saved Design" in PUBLIC
    assert "Review Name Drift" in PUBLIC
    assert "Change Channel Separator Only" in PUBLIC
    assert "Change One Style" not in PUBLIC
    assert "Fix Mismatched Names" not in PUBLIC


def test_rules_unlocks_surface_is_obvious() -> None:
    assert "Rules & Unlocks" in PUBLIC
    assert "Lock / Unlock Saved Rules" in PUBLIC
    assert "Unlock Saved Rules" in PUBLIC
    assert "Nothing is permanent" in PUBLIC
    assert "Locked category rules" in PUBLIC
    assert "Locked channel overrides" in PUBLIC
    assert "Editors & Locks" not in PUBLIC


def test_lock_manager_shows_exact_presets() -> None:
    assert "category_frame_id" in PUBLIC
    assert "Frame `{frame}`" in PUBLIC
    assert "Separator `{sep}`" in PUBLIC
    assert "Strength `{strength}/5`" in PUBLIC
    assert "Protection policy → Channel override → Category rule → Global preset" in PUBLIC


def test_separator_only_tool_explains_scope() -> None:
    assert "This tool changes only the **separator between an existing icon and channel name**" in PUBLIC
    assert "It keeps current emoji/icons, font, category frames, permissions, tickets, verification, and channel order unchanged." in PUBLIC
    assert "How to fix next" in PUBLIC


def test_old_safe_repair_test_expectations_updated() -> None:
    assert "Saved rules / locks" in SAFE_TEST
    assert "compares names against saved category/channel rules" in SAFE_TEST
    assert "reviews saved rules first" not in SAFE_TEST


if __name__ == "__main__":
    for test in (
        test_home_uses_clear_workflows_not_old_vague_labels,
        test_rules_unlocks_surface_is_obvious,
        test_lock_manager_shows_exact_presets,
        test_separator_only_tool_explains_scope,
        test_old_safe_repair_test_expectations_updated,
    ):
        test()
        print(f"PASS {test.__name__}")
