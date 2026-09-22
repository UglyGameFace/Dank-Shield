from __future__ import annotations

from collections import Counter
from pathlib import Path

from stoney_verify.services import server_design_plan_service as plan_service
from stoney_verify.services import server_design_repair_confidence as confidence


ROOT = Path(__file__).resolve().parents[1]
V2 = (ROOT / "stoney_verify" / "commands_ext" / "public_design_studio_v2.py").read_text(encoding="utf-8")


def _safe(before: str, after: str) -> dict[str, object]:
    return {
        "status": "changed",
        "kind": "text",
        "before": before,
        "after": after,
        "warnings": [],
        "blockers": [],
    }


def _aesthetic_block(before: str, after: str) -> dict[str, object]:
    return {
        "status": "changed",
        "kind": "text",
        "before": before,
        "after": after,
        "warnings": [],
        "blockers": [],
    }


def test_mixed_smart_repair_keeps_safe_half_actionable() -> None:
    items = [
        _safe("gaming-lounge-global", "gaming-lounge-globals"),
        _safe("member-count-110", "member-count-111"),
        _safe("spam-blocked-0", "spam-blocked-1"),
        _safe("quarantined-0", "quarantined-1"),
        _safe("voice-count-10", "voice-count-11"),
        _safe("invite-count-5570", "invite-count-5571"),
        _aesthetic_block("👥 𝕄𝕖𝕞𝕓𝕖𝕣𝕤 110", "members-110"),
        _aesthetic_block("⛔ 𝕊𝕡𝕒𝕞 𝔹𝕝𝕠𝕔𝕜𝕖𝕕 0", "spam-blocked-0"),
        _aesthetic_block("🎟️ 𝕆𝕡𝕖𝕟 𝕋𝕚𝕔𝕜𝕖𝕥𝕤 0", "open-tickets-0"),
        _aesthetic_block("🔗 𝕀𝕟𝕧𝕚𝕥𝕖𝕤 5570", "invites-5570"),
        _aesthetic_block("🙋 ℂ𝕝𝕒𝕚𝕞𝕖𝕕 𝕋𝕚𝕔𝕜𝕖𝕥𝕤 0", "claimed-tickets-0"),
        _aesthetic_block("✅ ℂ𝕝𝕠𝕤𝕖𝕕 𝕋𝕚𝕔𝕜𝕖𝕥𝕤 3", "closed-tickets-3"),
    ]

    result = confidence.evaluate_repair_plan(items, context="smart_category_auto_detect")

    assert result["apply_allowed"] is False
    assert result["safe_apply_allowed"] is True
    assert result["label"] == "Partial"
    assert result["safe_count"] == 6
    assert result["blocked_count"] == 6

    guarded = plan_service._fail_closed_on_low_confidence(items, result)
    statuses = Counter(str(item.get("status")) for item in guarded)

    assert statuses["changed"] == 6
    assert statuses["failed"] == 6
    assert all(
        item.get("repair_confidence_classification") == confidence.SAFE_AUTO_FIX
        for item in guarded
        if item.get("status") == "changed"
    )
    assert all(
        item.get("repair_confidence_classification") != confidence.SAFE_AUTO_FIX
        for item in guarded
        if item.get("status") == "failed"
    )


def test_consistency_preview_explicitly_applies_only_ready_subset() -> None:
    assert "can_apply=has_changes" in V2
    assert 'allow_safe_subset = mode == "consistency_check_v2"' in V2
    assert "failed_items and not allow_safe_subset" in V2
    assert "blocked/review rows stay untouched" in V2
    assert "blocked/review/unchanged item(s) untouched" in V2
