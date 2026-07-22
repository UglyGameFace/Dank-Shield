from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import raidguard, spam_guard
from stoney_verify.members_new import sync_service


def test_spam_incident_feeds_real_behavior_into_member_risk(
    monkeypatch,
) -> None:
    member = SimpleNamespace(id=101, guild=SimpleNamespace(id=777))
    seen: list[dict] = []

    async def fake_record(recorded_member, behavior_context, reasons=None):
        assert recorded_member is member
        seen.append(
            {
                "behavior": dict(behavior_context),
                "reasons": list(reasons or []),
            }
        )
        return {
            "review_verdict": "HIGH-CONFIDENCE SPAM ACCOUNT",
            "spam_risk_score": 88,
            "alt_evidence_tier": "clear",
        }

    monkeypatch.setattr(
        raidguard,
        "record_member_security_behavior",
        fake_record,
    )

    result = asyncio.run(
        spam_guard._record_security_behavior_for_incident(
            member,
            {
                "spam_guard_triggered": True,
                "url_flood": True,
                "cross_channel_flood": True,
            },
            ["rapid URLs"],
        )
    )

    assert result["spam_risk_score"] == 88
    assert seen == [
        {
            "behavior": {
                "spam_guard_triggered": True,
                "url_flood": True,
                "cross_channel_flood": True,
            },
            "reasons": ["rapid URLs"],
        }
    ]


def test_age_bucket_alone_cannot_create_alt_identity_cluster_key() -> None:
    key = sync_service._derive_alt_cluster_key_from_profile(
        {
            "same_age_bucket_count": 8,
            "age_bucket": "0-1d",
            "same_fingerprint_count": 0,
            "similar_name_count": 0,
        }
    )
    assert key is None
