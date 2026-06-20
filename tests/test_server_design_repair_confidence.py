from stoney_verify.services import server_design_repair_confidence as confidence


def test_blocks_decorative_category_to_plain_slug_without_fixed_mark_list():
    item = {
        "status": "changed",
        "kind": "category",
        "before": "✧✧ VIP Lounge / After Hours ✧✧",
        "after": "vip-lounge-after-hours",
    }

    result = confidence.score_repair_item(item, context="live_majority")

    assert result["classification"] == confidence.BLOCKED_AESTHETIC_DOWNGRADE
    assert result["confidence"] == 0


def test_blocks_unicode_style_loss():
    item = {
        "status": "changed",
        "kind": "category",
        "before": "𝔙𝔞𝔠𝔲𝔲𝔪 𝔖𝔢𝔞𝔩𝔢𝔡",
        "after": "vacuum-sealed",
    }

    result = confidence.score_repair_item(item, context="live_majority")

    assert result["classification"] == confidence.BLOCKED_AESTHETIC_DOWNGRADE


def test_blocks_discord_name_limit():
    item = {
        "status": "changed",
        "kind": "text",
        "before": "rules",
        "after": "x" * 101,
    }

    result = confidence.score_repair_item(item, context="live_majority")

    assert result["classification"] == confidence.BLOCKED_DISCORD_LIMIT


def test_evaluate_plan_disables_apply_when_blocked():
    items = [
        {
            "status": "changed",
            "kind": "category",
            "before": "START HERE / VERIFY",
            "after": "start-here-verify",
        },
        {
            "status": "changed",
            "kind": "category",
            "before": "SUPPORT TOOLS",
            "after": "support-tools",
        },
    ]

    result = confidence.evaluate_repair_plan(items, context="live_majority")

    assert result["apply_allowed"] is False
    assert result["blocked_count"] == 2
    assert result["label"] == "Blocked"


def test_small_drift_can_be_safe():
    item = {
        "status": "changed",
        "kind": "text",
        "before": "verifcation",
        "after": "verification",
    }

    result = confidence.score_repair_item(item, context="live_majority")

    assert result["classification"] == confidence.SAFE_AUTO_FIX
    assert result["confidence"] >= 80
