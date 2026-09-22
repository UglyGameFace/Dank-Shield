from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.commands_ext import public_design_studio as legacy
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
        _safe("online-count-5", "online-count-6"),
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

def test_bot_owned_live_stats_are_removed_from_design_detection(monkeypatch) -> None:
    category = SimpleNamespace(id=900, name="🛡️ DANK SHIELD STATS")
    stat_channel = SimpleNamespace(id=901, name="👥 Members: 110", category_id=900, category=category)
    ordinary = SimpleNamespace(id=902, name="gaming-lounge-global", category_id=0, category=None)

    class FakeGuild:
        id = 12345
        categories = [category]
        channels = [stat_channel, ordinary]

        @staticmethod
        def get_channel(channel_id: int):
            return {900: category, 901: stat_channel, 902: ordinary}.get(int(channel_id))

    async def fake_config(guild_id: int, *, refresh: bool = False):
        assert guild_id == 12345
        assert refresh is False
        return {
            "security_stats_display_enabled": True,
            "security_stats_category_id": "900",
            "security_stats_channel_ids": {"members": "901"},
        }

    monkeypatch.setattr(plan_service, "get_guild_config", fake_config)

    excluded = asyncio.run(plan_service._functional_design_resource_ids(FakeGuild()))
    assert excluded == {900, 901}

    filtered = plan_service._exclude_functional_items(
        [
            {"channel_id": "900", "status": "changed"},
            {"channel_id": "901", "status": "changed"},
            {"channel_id": "902", "status": "changed"},
        ],
        excluded,
    )
    assert [item["channel_id"] for item in filtered] == ["902"]

def test_drift_plan_excludes_live_stats_before_detection_and_preview(monkeypatch) -> None:
    category = SimpleNamespace(id=900, name="🛡️ DANK SHIELD STATS")
    stat_channel = SimpleNamespace(id=901, name="👥 Members: 110", category_id=900, category=category)
    ordinary = SimpleNamespace(id=902, name="gaming-lounge-global", category_id=0, category=None)

    class FakeGuild:
        id = 12346
        categories = [category]
        channels = [stat_channel, ordinary]

        @staticmethod
        def get_channel(channel_id: int):
            return {900: category, 901: stat_channel, 902: ordinary}.get(int(channel_id))

    async def fake_config(guild_id: int, *, refresh: bool = False):
        assert guild_id == 12346
        return {
            "security_stats_display_enabled": True,
            "security_stats_category_id": "900",
            "security_stats_channel_ids": {"members": "901"},
        }

    seen_records: list[dict[str, object]] = []

    def fake_live_records(_guild):
        return [
            {"id": "901", "category_id": "900", "kind": "text", "name": "👥 Members: 110"},
            {"id": "902", "category_id": "", "kind": "text", "name": "gaming-lounge-global"},
        ]

    def fake_category_options(_studio, options, records):
        seen_records.extend(dict(row) for row in records)
        return dict(options), {}

    async def fake_build(_guild, _options):
        return [
            {
                "channel_id": "901",
                "category_id": "900",
                "kind": "voice",
                "status": "changed",
                "before": "👥 Members: 110",
                "after": "members-110",
                "warnings": [],
                "blockers": [],
            },
            {
                "channel_id": "902",
                "category_id": "",
                "kind": "text",
                "status": "changed",
                "before": "gaming-lounge-global",
                "after": "gaming-lounge-globals",
                "warnings": [],
                "blockers": [],
            },
        ]

    monkeypatch.setattr(plan_service, "get_guild_config", fake_config)
    monkeypatch.setattr(plan_service, "live_records", fake_live_records)
    monkeypatch.setattr(plan_service.majority, "build_category_aware_options", fake_category_options)
    monkeypatch.setattr(plan_service.majority, "annotate_category_aware_plan_items", lambda _studio, rows, _options: rows)
    monkeypatch.setattr(legacy, "build_design_plan", fake_build)

    items, _options, _analysis = asyncio.run(plan_service.build_drift_repair_plan(FakeGuild(), {}))

    assert [row["id"] for row in seen_records] == ["902"]
    assert [item["channel_id"] for item in items] == ["902"]
    assert items[0]["status"] == "changed"
