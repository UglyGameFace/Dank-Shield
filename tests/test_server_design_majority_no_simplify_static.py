from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_majority_layout_guard.py").read_text()


def test_live_majority_blocks_decorative_simplification_apply():
    required = [
        "_looks_display_heading",
        "_looks_plain_slug",
        "_visual_downgrade_items",
        "_majority_apply_blocked",
        "Apply blocked — would simplify this server",
        "Apply is blocked because this preview would simplify styled section names",
        "not _majority_apply_blocked(items)",
    ]

    for phrase in required:
        assert phrase in SOURCE


def test_live_majority_recommendation_is_no_longer_blindly_live_majority():
    assert "For hand-built servers, choose **Use Live Majority**." not in SOURCE
    assert "Use **Live Majority** only when the preview keeps the current server look." in SOURCE


def test_patch_is_names_only_not_permission_or_config_repair():
    forbidden = [
        "set_permissions",
        "edit_permissions",
        "create_role",
        "delete_role",
        "create_text_channel",
        "create_category",
        "manage_roles",
        "manage_channels",
    ]

    lowered = SOURCE.lower()
    for phrase in forbidden:
        assert phrase not in lowered
