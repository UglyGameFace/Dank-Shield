from __future__ import annotations

from pathlib import Path

APPLIER = Path("tools/apply_p0_int_design_style_change_native_guard.py").read_text(encoding="utf-8")
STUDIO = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")


def test_style_change_applier_requires_native_design_helpers() -> None:
    assert "async def _guard_design_action(" in APPLIER
    assert "safe_send_interaction" in APPLIER
    assert "Apply exact-format guard migration first" in APPLIER


def test_style_change_applier_targets_issue_review_actions() -> None:
    for action_name in (
        "design.style_change.missing_icons_submit",
        "design.style_change.missing_icons.expired",
        "design.style_change.apply_safe_only",
        "design.style_change.apply_safe.expired",
        "design.style_change.fix_missing_icons_modal",
        "design.style_change.fix_missing.expired",
        "design.style_change.fix_missing.none",
        "design.style_change.fix_missing.too_many",
    ):
        assert action_name in APPLIER


def test_style_change_runtime_is_either_debt_or_applied() -> None:
    assert "class StyleChangeFixMissingEmojiModal" in STUDIO
    assert "class StyleChangeApplySafeOnlyButton" in STUDIO
    assert "class StyleChangeFixMissingEmojiButton" in STUDIO
    if "design.style_change.apply_safe_only" in STUDIO:
        for action_name in (
            "design.style_change.missing_icons_submit",
            "design.style_change.apply_safe_only",
            "design.style_change.fix_missing_icons_modal",
        ):
            assert action_name in STUDIO
    else:
        assert "This preview expired. Run Style Change again." in STUDIO
        assert "return await interaction.response.send_message" in STUDIO
