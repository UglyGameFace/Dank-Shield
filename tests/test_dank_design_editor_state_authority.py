from __future__ import annotations

from pathlib import Path

from stoney_verify.services import server_design_studio as studio
from stoney_verify.startup_guards import server_design_strict_layout_guard as strict_guard


PUBLIC_STUDIO = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")


def test_saved_owner_rules_are_not_rewritten_by_gothic_default_normalizer() -> None:
    options = {
        "theme_id": "gothic_clean",
        "format_lock_global": {"enabled": True, "font": "fraktur", "separator_id": "bar_heavy"},
        "category_format_locks": {"123": {"font": "fraktur", "separator_id": "bar_full"}},
        "channel_format_locks": {"456": {"font": "fraktur", "separator_id": "katakana_dot"}},
    }

    normalized = strict_guard._normalize_gothic_design_options(options)

    assert normalized == options
    assert normalized["channel_format_locks"]["456"]["separator_id"] == "katakana_dot"


def test_recommended_strength_four_applies_selected_category_frame() -> None:
    result = studio.build_styled_name(
        "gaming",
        kind="category",
        theme_id="gothic_clean",
        strength=4,
        category_frame_id="lenticular",
        font="normal",
        icon_mode="clear",
        exact_match=True,
    )

    assert result.status == "changed"
    assert result.after.startswith("【")
    assert result.after.endswith("】")


def test_manual_exact_editor_changes_force_exact_match() -> None:
    assert 'current["exact_match"] = True' in PUBLIC_STUDIO


def test_unsaved_exact_editor_seeds_from_selected_item_not_server_majority() -> None:
    assert "def _live_target_exact_lock(" in PUBLIC_STUDIO
    assert "target_lock = _live_target_exact_lock" in PUBLIC_STUDIO
    assert "elif target_lock:" in PUBLIC_STUDIO
    assert '"live_target": "Selected item current style"' in PUBLIC_STUDIO


def test_exact_editor_preview_does_not_reuse_whole_server_style_change_controls() -> None:
    start = PUBLIC_STUDIO.index("async def _save_exact_and_preview")
    end = PUBLIC_STUDIO.index("class ExactFormatEditorView", start)
    exact_preview = PUBLIC_STUDIO[start:end]
    assert "view=DesignPreviewView(" in exact_preview
    assert "StyleChangePreviewView" not in exact_preview


def test_exact_editor_apply_is_bound_to_the_preview_user_reviewed() -> None:
    assert "pending_created_at=created_at" in PUBLIC_STUDIO
    assert "if self.pending_created_at is not None:" in PUBLIC_STUDIO
    assert "This Apply button belongs to an older preview" in PUBLIC_STUDIO
