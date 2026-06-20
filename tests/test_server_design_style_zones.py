from stoney_verify.services import server_design_style_zones as zones
from stoney_verify.services import server_design_repair_confidence as confidence


def test_detects_generic_public_design_zones():
    cases = {
        "start-here": "onboarding",
        "welcome-and-rules": "onboarding",
        "verify-here": "verification",
        "verification-center": "verification",
        "support-tickets": "support_tickets",
        "ticket-archive": "support_tickets",
        "moderation-log": "safety_logs",
        "staff-notes": "safety_logs",
        "photo-gallery": "media_pics",
        "media-clips": "media_pics",
        "voice-lounge": "gaming_voice",
        "game-chat": "gaming_voice",
    }

    for name, expected in cases.items():
        assert zones.zone_for_name(name) == expected


def test_start_here_verify_is_onboarding_not_server_specific_verification():
    # Mixed onboarding + verification wording should be treated as a start area.
    # This prevents global logic from overfitting to one server's naming style.
    assert zones.zone_for_name("START HERE / VERIFY") == "onboarding"


def test_annotates_items_without_changing_status():
    items = [
        {"status": "changed", "kind": "category", "before": "ticket-archive", "after": "tickets-archive"},
        {"status": "unchanged", "kind": "text", "before": "photo-gallery", "after": "photo-gallery"},
    ]

    out = zones.annotate_items(items)

    assert out[0]["status"] == "changed"
    assert out[0]["design_zone"] == "support_tickets"
    assert out[1]["design_zone"] == "media_pics"


def test_confidence_reviews_sensitive_zones():
    item = {
        "status": "changed",
        "kind": "category",
        "before": "verification-center",
        "after": "verification",
    }

    result = confidence.score_repair_item(item, context="live_majority")

    assert result["classification"] in {
        confidence.REVIEW_ONLY,
        confidence.BLOCKED_AESTHETIC_DOWNGRADE,
    }
    assert result["classification"] != confidence.SAFE_AUTO_FIX


def test_zone_summary_text_is_human_readable():
    items = [
        {"kind": "text", "before": "photo-gallery"},
        {"kind": "text", "before": "game-chat"},
        {"kind": "category", "before": "ticket-archive"},
    ]

    text = zones.zone_summary_text(zones.annotate_items(items))

    assert "Media / pics" in text
    assert "Support / tickets" in text
