from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RECOMMEND = (ROOT / "stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")
FULL = (ROOT / "stoney_verify/commands_ext/public_setup_full_customization.py").read_text(encoding="utf-8")
GUARD = (ROOT / "stoney_verify/startup_guards/member_lifecycle_audit_context_guard.py").read_text(encoding="utf-8")


def test_recommended_setup_path_is_the_real_existing_server_menu() -> None:
    """Core Setup owns the deliberate existing-item mapper."""

    import ast

    tree = ast.parse(RECOMMEND)

    core_matches = [
        current
        for current in tree.body
        if isinstance(current, ast.ClassDef)
        and current.name == "AdvancedCoreSetupView"
    ]

    assert len(core_matches) == 1

    core_view = core_matches[0]
    core_source = (
        ast.get_source_segment(
            RECOMMEND,
            core_view,
        )
        or ""
    )

    assert 'label="Detailed Role / Channel Mapping"' in core_source
    assert 'custom_id="dank_setup_advanced_core:existing"' in core_source

    method_matches = [
        current
        for current in core_view.body
        if isinstance(
            current,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
        and current.name == "detailed_mapping"
    ]

    assert len(method_matches) == 1

    method_source = (
        ast.get_source_segment(
            RECOMMEND,
            method_matches[0],
        )
        or ""
    )

    assert "await _open_existing_server(interaction)" in method_source

    # The old competing Continue Setup mapping button was removed.
    assert "dank_setup_continue:existing" not in RECOMMEND


def test_guard_patches_recommended_existing_server_menu() -> None:
    assert "_patch_setup_existing_menu" in GUARD
    assert "rec._open_existing_server" in GUARD
    assert "FullChooseExistingView" in GUARD
    assert "Join / Leave Log" in GUARD


def test_join_leave_picker_is_visible_in_logs_and_channels() -> None:
    assert "VisibleLogStatusCustomizationView" in GUARD
    assert "VisibleChannelCustomizationPageTwo" in GUARD
    assert "Join / leave log channel — not welcome" in GUARD
    assert "Logs & Status" in FULL


def test_join_leave_picker_writes_authoritative_aliases() -> None:
    for key in (
        "join_leave_log_channel_id",
        "member_join_leave_log_channel_id",
        "member_lifecycle_log_channel_id",
        "member_log_channel_id",
        "member_logs_channel_id",
        "join_log_channel_id",
        "join_exit_log_channel_id",
        "welcome_exit_channel_id",
        "leave_log_channel_id",
        "welcome_leave_channel_id",
    ):
        assert key in GUARD, f"setup menu patch missing {key}"


if __name__ == "__main__":
    for test in (
        test_recommended_setup_path_is_the_real_existing_server_menu,
        test_guard_patches_recommended_existing_server_menu,
        test_join_leave_picker_is_visible_in_logs_and_channels,
        test_join_leave_picker_writes_authoritative_aliases,
    ):
        test()
        print(f"PASS {test.__name__}")
