from pathlib import Path


CONFIDENCE = Path("stoney_verify/services/server_design_repair_confidence.py").read_text()
PLAN = Path("stoney_verify/services/server_design_plan_service.py").read_text()


def test_smart_auto_detect_blocks_decorative_simplification_apply() -> None:
    for phrase in (
        "_display_score",
        "_looks_plain_slug",
        "_is_aesthetic_downgrade",
        "BLOCKED_AESTHETIC_DOWNGRADE",
        '"smart_category_auto_detect"',
        "Would simplify or strip this server's existing visual style.",
    ):
        assert phrase in CONFIDENCE

    assert "_fail_closed_on_low_confidence" in PLAN
    assert 'item["status"] = "failed"' in PLAN


def test_smart_auto_detect_recommendation_is_category_aware() -> None:
    assert "build_category_aware_options" in PLAN
    assert "annotate_category_aware_plan_items" in PLAN
    assert "Mixed categories keep their own" in PLAN
    assert "respect_saved_rules=True" in PLAN


def test_native_repair_services_are_names_only_not_permission_or_config_repair() -> None:
    forbidden = (
        "set_permissions",
        "edit_permissions",
        "create_role",
        "delete_role",
        "create_text_channel",
        "create_category",
        "manage_roles",
        "manage_channels",
    )
    lowered = (CONFIDENCE + PLAN).lower()
    for phrase in forbidden:
        assert phrase not in lowered
