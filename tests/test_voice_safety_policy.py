from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from stoney_verify.community_voice_caption_runtime import (
    CommunityVoiceCaptionManager,
    voice_safety_scope_id,
    voice_safety_host_enabled,
)
from stoney_verify.commands_ext.public_protection_center import ProtectionCenterView
from stoney_verify.commands_ext.public_voice_safety import (
    VoiceSafetyConsentView,
    VoiceSafetyStaffView,
    _readiness,
)
from stoney_verify.voice_safety_policy import (
    VoiceSafetyAdvisor,
    VoiceSafetyVerdict,
    parse_voice_safety_verdict,
)


ROOT = Path(__file__).resolve().parents[1]


def test_voice_safety_policy_rejects_ambiguous_or_identity_only_results() -> None:
    assert parse_voice_safety_verdict({"category": "none", "severity": "none", "reason": "gay dating"}) is None
    assert parse_voice_safety_verdict({"category": "orientation", "severity": "urgent", "reason": "identity"}) is None
    assert parse_voice_safety_verdict({"category": "harassment", "severity": "unknown", "reason": "bad"}) is None
    assert parse_voice_safety_verdict({"category": "harassment", "severity": "review", "reason": ""}) is None
    assert parse_voice_safety_verdict({"category": "harassment", "severity": "review", "reason": "a" * 221}) is None
    assert parse_voice_safety_verdict("raw transcript") is None
    assert parse_voice_safety_verdict(
        {"category": "unwanted_sexual_comments", "severity": "review", "reason": "Repeated directed remarks."}
    ) == VoiceSafetyVerdict("unwanted_sexual_comments", "review", "Repeated directed remarks.")


def test_voice_safety_context_isolated_by_speaker_with_bounded_cooldown() -> None:
    async def run() -> None:
        seen = []
        clock = [100.0]

        async def classify(text):
            seen.append(text)
            return VoiceSafetyVerdict("harassment", "review", "Targeted harassment")

        advisor = VoiceSafetyAdvisor(classify=classify, clock=lambda: clock[0], cooldown_seconds=90)
        check = lambda uid: uid in {11, 22}
        first = await advisor.inspect(11, "Stop saying that to them", 0.95, is_admitted=check)
        assert first is not None
        assert await advisor.inspect(11, "Stop saying that to them again", 0.95, is_admitted=check) is None
        other = await advisor.inspect(22, "Separate speaker's aggressive remarks", 0.95, is_admitted=check)
        assert other is not None
        assert "Separate speaker" not in seen[1]
        clock[0] += 100
        assert await advisor.inspect(11, "Stop targeting the same member now", 0.95, is_admitted=check)
        assert advisor.alerts == 3
        assert all(len(s) <= 1200 for s in seen)
        advisor.close()
        assert await advisor.inspect(11, "More harmful remarks repeated", 0.95, is_admitted=check) is None

    asyncio.run(run())


def test_voice_safety_abstains_on_unclear_and_nonconsented_speech() -> None:
    async def run() -> None:
        calls = []
        async def classify(text):
            calls.append(text)
            return VoiceSafetyVerdict("harassment", "review", "Targeted harassment")
        advisor = VoiceSafetyAdvisor(classify=classify)
        for transcript, confidence, admitted in [
            ("[unclear audio]", 0.99, True),
            ("Barely audible and mistaken", 0.3, True),
            ("No consent by this user", 0.99, False),
            ("short", 1.0, True),
        ]:
            assert await advisor.inspect(3, transcript, confidence, is_admitted=lambda uid: admitted) is None
        assert calls == []
    asyncio.run(run())


def test_voice_safety_opt_out_invalidates_in_flight_review() -> None:
    async def run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        async def classify(text):
            started.set()
            await release.wait()
            return VoiceSafetyVerdict("graphic_sexual_content", "review", "Graphic conduct.")
        advisor = VoiceSafetyAdvisor(classify=classify)
        admitted = {47}
        task = asyncio.create_task(advisor.inspect(47, "Graphic description about something", 0.99, is_admitted=lambda uid: uid in admitted))
        await started.wait()
        admitted.remove(47)
        advisor.revoke(47)
        release.set()
        assert await task is None
        assert advisor.alerts == 0
        assert advisor._segments == {}
    asyncio.run(run())


def test_voice_safety_never_reuses_caption_consent(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.profile_card_service as profiles

        async def fake_prefs(gid, uid, *, refresh=False):
            return {"settings": {"live_captions_auto_opt_in": True}}
        monkeypatch.setattr(profiles, "get_profile_guild_settings", fake_prefs)
        manager = CommunityVoiceCaptionManager(SimpleNamespace())
        assert await manager.voice_safety_consented(1, 2) is False
        assert manager._sessions == {}
    asyncio.run(run())


def test_voice_safety_is_separate_gated_scope(monkeypatch) -> None:
    assert voice_safety_scope_id(5) == "voice_safety:5"
    monkeypatch.delenv("DANK_VOICE_SAFETY_ENABLED", raising=False)
    assert not voice_safety_host_enabled()
    monkeypatch.setenv("DANK_VOICE_SAFETY_ENABLED", "true")
    assert voice_safety_host_enabled()
    source = (ROOT / "stoney_verify" / "community_voice_caption_runtime.py").read_text()
    assert 'not voice_safety_host_enabled() or not live_captions_enabled()' in source
    assert "connect_receive_client(voice_channel, bridge)" in source
    assert "Another Live Captions session in this server already owns the voice receiver." in source


def test_voice_safety_ui_is_accessible_from_existing_protection_and_persistent() -> None:
    labels = {child.label for child in ProtectionCenterView(author_id=1).children if getattr(child, "label", None)}
    assert "Voice Safety" in labels
    staff = VoiceSafetyStaffView(1)
    selects = [child for child in staff.children if isinstance(child, discord.ui.ChannelSelect)]
    assert len(selects) == 3
    assert {"Start / Stop Review", "Enable / Disable Setup"}.issubset({
        child.label for child in staff.children if getattr(child, "label", None)
    })
    public = VoiceSafetyConsentView()
    assert public.timeout is None
    assert {child.custom_id for child in public.children} == {
        "dank:voice_safety:consent_on:v1",
        "dank:voice_safety:consent_off:v1",
    }


def test_staff_readiness_fails_when_host_gates_are_disabled(monkeypatch) -> None:
    monkeypatch.delenv("DANK_VOICE_SAFETY_ENABLED", raising=False)
    assert _readiness(SimpleNamespace(), {})[0] is False
