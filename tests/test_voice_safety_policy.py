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
    VoiceSafetyEndedView,
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
        assert "Separate speaker" in seen[1]
        assert "Stop saying that" not in seen[1]
        assert "Separate speaker" not in seen[0]
        clock[0] += 100
        assert await advisor.inspect(11, "Stop targeting the same member now", 0.95, is_admitted=check)
        assert advisor.alerts == 3
        assert all(len(s) <= 1200 for s in seen)
        advisor.close()
        assert await advisor.inspect(11, "More harmful remarks repeated", 0.95, is_admitted=check) is None

    asyncio.run(run())


def test_voice_safety_model_requests_are_rate_limited_before_network_use() -> None:
    async def run() -> None:
        clock = [1_000.0]
        calls = []
        async def classify(text):
            calls.append(text)
            return VoiceSafetyVerdict("harassment", "review", "Targeted speech")
        advisor = VoiceSafetyAdvisor(classify=classify, clock=lambda: clock[0])
        permitted = lambda uid: True
        await advisor.inspect(1, "Some clearly targeted conduct", 0.99, is_admitted=permitted)
        assert advisor.requests == 1
        assert await advisor.inspect(1, "A second fragment just after", 0.99, is_admitted=permitted) is None
        assert advisor.requests == 1
        clock[0] += 7
        await advisor.inspect(1, "Additional context for review", 0.99, is_admitted=permitted)
        assert advisor.requests == 2
        for uid in range(2, 40):
            await advisor.inspect(uid, "Long sentence for a review triage", 0.99, is_admitted=permitted)
        assert advisor.requests == 30
        clock[0] += 61
        await advisor.inspect(40, "Later statement requiring triage", 0.99, is_admitted=permitted)
        assert advisor.requests == 31
        advisor.revoke(1)
        assert 1 not in advisor._last_inspected
        advisor.close()
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


def test_voice_safety_never_reuses_caption_consent_or_restores_from_database(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.profile_card_service as profiles

        async def fake_prefs(gid, uid, *, refresh=False):
            return {"settings": {"live_captions_auto_opt_in": True}}
        monkeypatch.setattr(profiles, "get_profile_guild_settings", fake_prefs)
        manager = CommunityVoiceCaptionManager(SimpleNamespace())
        state = SimpleNamespace(guild_id=1, scope_kind="voice_safety", safety_consents=set())
        channel = SimpleNamespace(members=[SimpleNamespace(id=2, bot=False)])
        assert await manager._restore_auto_opt_ins(state, channel) == 0
        assert state.safety_consents == set()
    asyncio.run(run())


def test_voice_safety_one_time_agreement_persists_without_active_session(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.community_voice_caption_runtime as runtime

        values = {}
        async def save(gid, uid, enabled, *, source="voice_safety_notice_v1"):
            values[(gid, uid)] = enabled
            return enabled
        monkeypatch.setattr(runtime, "save_voice_safety_agreement", save)
        manager = CommunityVoiceCaptionManager(SimpleNamespace())
        assert await manager.set_voice_safety_consent(3, 4, True) is True
        assert values == {(3, 4): True}
        assert await manager.set_voice_safety_consent(3, 4, False) is False
        assert values == {(3, 4): False}
    asyncio.run(run())


def test_voice_safety_agreement_requires_explicit_disclosed_action(monkeypatch) -> None:
    import stoney_verify.voice_safety_consent as consent
    from stoney_verify.verification_new.basic_verify import (
        build_basic_verify_embed, BasicVerifyButton, BasicVerifyView,
    )

    guild = SimpleNamespace(get_channel=lambda key: None, text_channels=[])
    configured = build_basic_verify_embed(guild, {"voice_safety_enabled": True})
    regular = build_basic_verify_embed(guild, {})
    assert configured.title == "🛡️ Verify Your Access"
    assert len(configured.fields) == 1
    assert len(str(configured.to_dict())) < 1200
    voice_copy = str(configured.fields[0].value)
    assert all(
        phrase in voice_copy
        for phrase in (
            "inappropriate", "hateful", "harassing", "lewd", "sexually explicit",
            "offensive", "Google Gemini", "private safety alerts", "raw audio",
            "Decline & Exit Server", "10-second",
        )
    )
    assert "Gemini paid API" not in str(configured.to_dict())
    assert "Before you tap Verify" not in str(configured.to_dict())
    assert "What happens when you verify" not in str(configured.to_dict())
    assert "Agreement v1" not in str(regular.to_dict())
    assert "Tap **Verify**" in str(regular.description)
    assert "Agree & Verify" in str(configured.description)
    assert consent.verification_message_discloses_voice_safety(
        SimpleNamespace(embeds=[configured])
    )
    assert not consent.verification_message_discloses_voice_safety(
        SimpleNamespace(embeds=[regular])
    )
    assert not consent.verification_message_discloses_voice_safety(None)
    assert "Agree & Verify" in {child.label for child in BasicVerifyView(voice_safety_agreement=True).children}
    assert BasicVerifyButton().label == "Verify"


def test_voice_safety_agreement_does_not_infer_from_verified_role(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.profile_card_service as profiles
        from stoney_verify.voice_safety_consent import read_voice_safety_agreement

        state = {"settings": {"live_captions_auto_opt_in": True, "verified": True}}
        async def read(*args, **kwargs):
            return state
        monkeypatch.setattr(profiles, "get_profile_guild_settings", read)
        assert not await read_voice_safety_agreement(1, 2)
        state["settings"]["voice_safety_agreement_v1"] = {"accepted": True, "version": 1, "source": "basic_verify_v1", "acted_at": "2026-10-12T00:00:00+00:00"}
        assert await read_voice_safety_agreement(1, 2)
        state["settings"]["voice_safety_agreement_v1"] = {"accepted": False, "version": 1}
        assert not await read_voice_safety_agreement(1, 2)
        state["settings"]["voice_safety_agreement_v1"] = {"accepted": True, "version": 2}
        assert not await read_voice_safety_agreement(1, 2)
    asyncio.run(run())


def test_voice_safety_is_separate_gated_scope(monkeypatch) -> None:
    assert voice_safety_scope_id(5) == "voice_safety:5"
    monkeypatch.delenv("DANK_VOICE_SAFETY_ENABLED", raising=False)
    assert not voice_safety_host_enabled()
    monkeypatch.setenv("DANK_VOICE_SAFETY_ENABLED", "true")
    assert not voice_safety_host_enabled()
    monkeypatch.setenv("DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED", "true")
    assert not voice_safety_host_enabled()
    monkeypatch.setenv("DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED", "true")
    assert voice_safety_host_enabled()
    source = (ROOT / "stoney_verify" / "community_voice_caption_runtime.py").read_text()
    assert 'not voice_safety_host_enabled() or not live_captions_enabled()' in source
    assert "connect_receive_client(voice_channel, bridge)" in source
    assert "Another Live Captions session in this server already owns the voice receiver." in source


def test_voice_safety_ui_is_accessible_from_existing_protection_and_persistent() -> None:
    labels = {child.label for child in ProtectionCenterView(author_id=1, section="tools").children if getattr(child, "label", None)}
    assert "Voice Safety" in labels
    staff = VoiceSafetyStaffView(1)
    assert not any(isinstance(child, discord.ui.ChannelSelect) for child in staff.children)
    assert {"Monitored Voice Channel", "Private Staff Alerts", "Public Member Notice"} <= {
        child.label for child in staff.children if getattr(child, "label", None)
    }
    assert all(int(getattr(child, "row", 0) or 0) <= 4 for child in staff.children)
    assert {"Start / Stop Review", "Enable / Disable Setup"}.issubset({
        child.label for child in staff.children if getattr(child, "label", None)
    })
    public = VoiceSafetyConsentView()
    assert public.timeout is None
    ended = VoiceSafetyEndedView()
    assert ended.opt_in.disabled is True
    assert ended.opt_out.disabled is False
    assert {child.custom_id for child in public.children} == {
        "dank:voice_safety:consent_on:v1",
        "dank:voice_safety:consent_off:v1",
    }


def test_staff_readiness_fails_when_host_gates_are_disabled(monkeypatch) -> None:
    monkeypatch.delenv("DANK_VOICE_SAFETY_ENABLED", raising=False)
    assert _readiness(SimpleNamespace(), {})[0] is False


def test_voice_safety_manager_end_to_end_consent_and_staff_alert(monkeypatch) -> None:
    """Simulate the canonical receiver path without network or unconsented audio."""
    async def run() -> None:
        import stoney_verify.community_voice_caption_runtime as runtime
        import stoney_verify.guild_config as guild_config

        class FakeVoiceChannel:
            def __init__(self):
                self.id = 71
                self.mention = "<#71>"
                self.members = []
            def permissions_for(self, member):
                return SimpleNamespace(connect=True, view_channel=True)

        class FakeTextChannel:
            def __init__(self, channel_id):
                self.id = channel_id
                self.sent = []
                self.edits = []
            def permissions_for(self, member):
                return SimpleNamespace(
                    view_channel=member != "everyone" or self.id != 72,
                    send_messages=True,
                )
            async def send(self, content, **kwargs):
                self.sent.append((content, kwargs))
                return SimpleNamespace(id=77)
            def get_partial_message(self, message_id):
                async def edit(**kwargs):
                    self.edits.append((message_id, kwargs))
                return SimpleNamespace(edit=edit)

        voice = FakeVoiceChannel()
        staff = FakeTextChannel(72)
        notice = FakeTextChannel(73)
        member = SimpleNamespace(
            id=99, bot=False, voice=SimpleNamespace(channel=voice),
        )
        voice.members = [member, SimpleNamespace(id=100, bot=False)]

        class FakeGuild:
            id = 5
            default_role = "everyone"
            me = "bot"
            def get_member(self, uid):
                return member if uid == 99 else None
            def get_channel(self, cid):
                return {71: voice, 72: staff, 73: notice}.get(int(cid))
            def get_thread(self, cid):
                return None

        guild = FakeGuild()
        bot = SimpleNamespace(get_guild=lambda gid: guild if gid == 5 else None, get_channel=lambda cid: None)
        cfg = {
            "voice_safety_enabled": True,
            "voice_safety_voice_channel_id": 71,
            "voice_safety_staff_channel_id": 72,
            "voice_safety_notice_channel_id": 73,
        }
        async def get_config(gid, *, refresh=False):
            return cfg
        monkeypatch.setattr(guild_config, "get_guild_config", get_config)
        monkeypatch.setattr(runtime, "voice_safety_host_enabled", lambda: True)
        monkeypatch.setattr(runtime, "live_captions_enabled", lambda: True)
        monkeypatch.setattr(runtime, "voice_receive_capability", lambda: SimpleNamespace(available=True, reason=""))
        monkeypatch.setattr(runtime.discord, "VoiceChannel", FakeVoiceChannel)
        monkeypatch.setattr(runtime.discord, "TextChannel", FakeTextChannel)

        class FakeTranscriber:
            async def prepare_user(self, uid):
                return None
            async def close(self):
                return None

        class FakeEngine:
            def __init__(self, transcriber, publish, **kwargs):
                self.transcriber = transcriber
                self.publish = publish
                self.allowed = set()
            def submit(self, frame):
                pass
            def start(self):
                pass
            def allow_user(self, uid):
                self.allowed.add(uid)
            async def revoke_user(self, uid):
                self.allowed.discard(uid)
            async def close(self):
                self.allowed.clear()

        class FakeClient:
            def is_connected(self):
                return False

        async def connect(channel, bridge):
            return FakeClient()

        async def classify(text):
            if "unwanted explicit remarks" in text:
                return VoiceSafetyVerdict("unwanted_sexual_comments", "review", "Repeated unwanted remarks")
            return None

        monkeypatch.setattr(runtime, "gemini_live_transcriber_from_env", lambda **kwargs: FakeTranscriber())
        monkeypatch.setattr(runtime, "CaptionEngine", FakeEngine)
        monkeypatch.setattr(runtime, "connect_receive_client", connect)
        monkeypatch.setattr(runtime, "disconnect_receive_client", lambda client: None)
        monkeypatch.setattr(runtime, "VoiceSafetyAdvisor", lambda: VoiceSafetyAdvisor(classify=classify))
        monkeypatch.setenv("GEMINI_API_KEY", "fake-ci-key")

        agreements = {}
        async def save(gid, uid, enabled, *, source="voice_safety_notice_v1"):
            agreements[(gid, uid)] = bool(enabled)
            return bool(enabled)
        async def read(gid, uid):
            return bool(agreements.get((gid, uid), False))
        monkeypatch.setattr(runtime, "save_voice_safety_agreement", save)
        monkeypatch.setattr(runtime, "read_voice_safety_agreement", read)
        manager = CommunityVoiceCaptionManager(bot)
        async def preferences(*args, **kwargs):
            return {"language_code": ""}
        manager.preferences_for_user = preferences

        state = await manager.start_voice_safety(
            guild_id=5, voice_channel_id=71, staff_alert_channel_id=72, notice_channel_id=73
        )
        assert state.scope_kind == "voice_safety"
        assert state.notice_message_id == 77
        assert len(notice.sent) == 1
        assert state.bridge.opted_in_user_ids() == ()
        await state.engine.publish(99, "unwanted explicit remarks directed at a member", 0.98)
        assert staff.sent == []  # no consent, no alert
        assert await manager.set_voice_safety_consent(5, 99, True) is True
        assert state.bridge.is_opted_in(99)
        await state.engine.publish(99, "unwanted explicit remarks directed at a member", 0.98)
        assert len(staff.sent) == 1
        assert "staff review only" in staff.sent[0][0]
        assert "unwanted_sexual_comments" in staff.sent[0][0]
        assert staff.sent[0][1]["allowed_mentions"].everyone is False
        assert await manager.set_voice_safety_consent(5, 99, False) is False
        assert not state.bridge.is_opted_in(99)
        await state.engine.publish(99, "unwanted explicit remarks directed at a member", 0.98)
        assert len(staff.sent) == 1
        await manager.stop(voice_safety_scope_id(5))
        assert "review ended" in notice.edits[0][1]["content"]
        assert state.safety_consents == set()

    asyncio.run(run())


def test_voice_safety_auto_start_uses_configured_channel_and_single_owner(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.community_voice_caption_runtime as runtime
        import stoney_verify.guild_config as config_service

        class FakeVoice:
            id = 22
            members = [
                SimpleNamespace(id=51, bot=False),
                SimpleNamespace(id=52, bot=False),
            ]
            def permissions_for(self, member):
                return SimpleNamespace(connect=True, view_channel=True)

        class FakeGuild:
            id = 17
            me = "bot"
            voice_channels = [FakeVoice()]
            def get_channel(self, channel_id):
                return self.voice_channels[0] if channel_id == 22 else None

        guild = FakeGuild()
        bot = SimpleNamespace(guilds=[guild], get_guild=lambda gid: guild)
        cfg = {
            "voice_safety_enabled": True,
            "voice_safety_voice_channel_id": 22,
            "voice_safety_staff_channel_id": 23,
            "voice_safety_notice_channel_id": 24,
        }

        async def get_config(gid, **kwargs):
            return cfg

        monkeypatch.setattr(config_service, "get_guild_config", get_config)
        monkeypatch.setattr(runtime, "voice_safety_host_enabled", lambda: True)
        monkeypatch.setattr(runtime, "live_captions_enabled", lambda: True)
        monkeypatch.setattr(runtime.discord, "VoiceChannel", FakeVoice)
        manager = CommunityVoiceCaptionManager(bot)
        called = []

        async def begin(**kwargs):
            called.append(kwargs)
            state = SimpleNamespace(
                session_id=voice_safety_scope_id(17),
                scope_kind="voice_safety",
            )
            manager._sessions[state.session_id] = state
            manager._guild_owner[17] = state.session_id
            return state

        monkeypatch.setattr(manager, "start_voice_safety", begin)
        assert await manager.maybe_auto_start_voice_safety(guild, joined_voice_id=999) is False
        assert await manager.maybe_auto_start_voice_safety(guild, joined_voice_id=22) is True
        assert len(called) == 1 and called[0]["automatic"] is True
        assert await manager.maybe_auto_start_voice_safety(guild, joined_voice_id=22) is False
        manager._sessions.clear()
        manager._guild_owner.clear()
        manager.pause_voice_safety_auto_start(17)
        assert await manager.maybe_auto_start_voice_safety(guild, joined_voice_id=22) is False
        manager.pause_voice_safety_auto_start(17, False)
        await manager.restore_voice_safety_after_ready()
        assert len(called) == 2

    asyncio.run(run())


def test_voice_safety_auto_start_fails_closed_on_disabled_host_and_guild(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.community_voice_caption_runtime as runtime
        import stoney_verify.guild_config as guild_config

        class FakeVoice:
            id = 42
            members = [SimpleNamespace(id=2, bot=False)]

        guild = SimpleNamespace(id=7, voice_channels=[FakeVoice()], get_channel=lambda x: FakeVoice())
        bot = SimpleNamespace(guilds=[guild])
        manager = CommunityVoiceCaptionManager(bot)
        monkeypatch.setattr(runtime.discord, "VoiceChannel", FakeVoice)
        monkeypatch.setattr(runtime, "voice_safety_host_enabled", lambda: False)
        monkeypatch.setattr(runtime, "live_captions_enabled", lambda: True)
        assert not await manager.maybe_auto_start_voice_safety(guild, joined_voice_id=42)
        monkeypatch.setattr(runtime, "voice_safety_host_enabled", lambda: True)

        async def get_config(gid, **kwargs):
            return {"voice_safety_enabled": False, "voice_safety_voice_channel_id": 42}

        monkeypatch.setattr(guild_config, "get_guild_config", get_config)
        assert not await manager.maybe_auto_start_voice_safety(guild, joined_voice_id=42)
        assert manager._sessions == {}

    asyncio.run(run())


def test_basic_verify_records_voice_agreement_only_from_disclosed_panel(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.verification_new.basic_verify as verify
        import stoney_verify.community_voice_caption_runtime as runtime

        class FakeMember:
            id = 42

        monkeypatch.setattr(verify.discord, "Member", FakeMember)
        monkeypatch.setattr(verify, "_claim_basic_verify_interaction", lambda interaction: True)

        async def ack(interaction):
            return True

        async def apply(member):
            return True, "Verified successfully"

        replied = []
        async def reply(interaction, message, *, ok):
            replied.append((message, ok))

        async def cfg(gid, **kwargs):
            return {"voice_safety_enabled": True}

        recorded = []
        class FakeManager:
            async def set_voice_safety_consent(self, gid, uid, enabled, *, source="voice_safety_notice_v1"):
                recorded.append((gid, uid, enabled))
                return enabled

        monkeypatch.setattr(verify, "_ack", ack)
        monkeypatch.setattr(verify, "_reply", reply)
        monkeypatch.setattr(verify, "apply_basic_verification", apply)
        monkeypatch.setattr(verify, "get_guild_config", cfg)
        monkeypatch.setattr(runtime, "ensure_community_voice_caption_manager", lambda bot: FakeManager())

        guild = SimpleNamespace(id=8)
        member = FakeMember()
        def interaction(message_id, embed):
            return SimpleNamespace(
                id=message_id, data={"custom_id": verify.BASIC_VERIFY_CUSTOM_ID},
                guild=guild, user=member, client=object(),
                message=SimpleNamespace(embeds=[embed]),
                channel=SimpleNamespace(id=2),
            )

        from stoney_verify.verification_new.basic_verify import build_basic_verify_embed

        original = build_basic_verify_embed(SimpleNamespace(get_channel=lambda key: None, text_channels=[]), {})
        disclosed = build_basic_verify_embed(SimpleNamespace(get_channel=lambda key: None, text_channels=[]), {"voice_safety_enabled": True})

        assert await verify.maybe_handle_basic_verify_interaction(interaction(100, original)) is True
        assert recorded == []
        assert await verify.maybe_handle_basic_verify_interaction(interaction(101, disclosed)) is True
        assert recorded == [(8, 42, True)]
        assert "one-time Voice Safety agreement" in replied[-1][0]

    asyncio.run(run())



def test_voice_safety_failed_optout_stops_all_review_and_revokes_immediately(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.community_voice_caption_runtime as runtime
        from stoney_verify.community_voice_receive import VoiceReceiveUnavailable

        async def failing_save(*args, **kwargs):
            raise RuntimeError("consent storage unavailable")

        monkeypatch.setattr(runtime, "save_voice_safety_agreement", failing_save)
        manager = CommunityVoiceCaptionManager(SimpleNamespace())
        state = SimpleNamespace(
            session_id="voice_safety:1", scope_kind="voice_safety",
            safety_consents={2}, safety_consent_epochs={},
        )
        manager._sessions[state.session_id] = state
        manager._guild_owner[1] = state.session_id
        revoked, stopped = [], []

        async def disable(st, uid):
            revoked.append(uid)

        async def stop(sid):
            stopped.append(sid)

        monkeypatch.setattr(manager, "_disable_user_for_state", disable)
        monkeypatch.setattr(manager, "stop", stop)

        try:
            await manager.set_voice_safety_consent(1, 2, False)
            assert False, "failure must be surfaced"
        except VoiceReceiveUnavailable:
            pass
        assert revoked == [2]
        assert stopped == [state.session_id]
        assert state.safety_consents == set()
        assert state.safety_consent_epochs[2] == 1

    asyncio.run(run())


def test_voice_safety_revocation_during_transcriber_prewarm_cannot_admit_pcm(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.community_voice_caption_runtime as runtime

        uid = 17
        member = SimpleNamespace(
            id=uid, voice=SimpleNamespace(channel=SimpleNamespace(id=7)),
        )
        guild = SimpleNamespace(get_member=lambda key: member if key == uid else None)
        manager = CommunityVoiceCaptionManager(
            SimpleNamespace(get_guild=lambda key: guild)
        )

        class FakeBridge:
            opted = False
            def is_opted_in(self, user_id):
                return self.opted
            def opted_in_user_ids(self):
                return ()
            def opt_in(self, user_id):
                self.opted = True

        class FakeTranscriber:
            async def prepare_user(self, user_id):
                state.safety_consent_epochs[uid] += 1

        class FakeEngine:
            transcriber = FakeTranscriber()
            allowed = False
            def allow_user(self, user_id):
                self.allowed = True

        state = SimpleNamespace(
            session_id="voice_safety:1", guild_id=1, voice_channel_id=7,
            scope_kind="voice_safety", bridge=FakeBridge(),
            engine=FakeEngine(), safety_consent_epochs={uid: 0},
            safety_consents=set(),
        )

        manager._sessions[state.session_id] = state
        async def prefs(*args, **kwargs):
            return {"language_code": ""}
        async def agreement(*args, **kwargs):
            return True
        monkeypatch.setattr(manager, "preferences_for_user", prefs)
        monkeypatch.setattr(runtime, "read_voice_safety_agreement", agreement)
        await manager._enable_user_for_state(state, uid)
        assert not state.bridge.opted
        assert not state.engine.allowed
        assert state.safety_consents == set()

    asyncio.run(run())


def test_voice_safety_uses_search_safe_paged_browser_for_decorated_channel_names(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.commands_ext.public_voice_safety as ui
        from stoney_verify.ui.resource_browser import DankGuildResourceBrowserView
        from stoney_verify.services import naming_identity

        channels = [
            SimpleNamespace(
                id=100 + index,
                name=f"🗨️channel-{index}",
                type=discord.ChannelType.text,
                category=None,
                mention=f"<#{100 + index}>",
            )
            for index in range(30)
        ]
        channels.append(
            SimpleNamespace(
                id=900, name="「📰」𝔾𝕖𝕟𝕖𝕣𝕒𝕝-ℕ𝕖𝕨𝕤",
                type=discord.ChannelType.text, category=None, mention="<#900>",
            )
        )
        voice = SimpleNamespace(
            id=901, name="🔊 𝓣𝓱𝓮 𝓗𝓸𝓽𝓫𝓸𝔁",
            type=discord.ChannelType.voice, category=None, mention="<#901>",
        )
        channels.append(voice)
        guild = SimpleNamespace(id=9, channels=channels)
        actor = SimpleNamespace(id=42)
        captured = {}

        class Response:
            def is_done(self):
                return False
            async def edit_message(self, **kwargs):
                captured.update(kwargs)
            async def send_message(self, *args, **kwargs):
                raise AssertionError((args, kwargs))

        interaction = SimpleNamespace(
            guild=guild, user=actor, client=object(), response=Response(),
        )
        monkeypatch.setattr(ui, "_staff", lambda interaction: True)
        monkeypatch.setattr(
            ui, "ensure_community_voice_caption_manager",
            lambda bot: SimpleNamespace(status_for_guild=lambda gid: {"active": False}),
        )
        async def no_saved_aliases(guild_id):
            return {}
        monkeypatch.setattr(naming_identity, "get_search_alias_index", no_saved_aliases)

        await ui._open_voice_safety_channel_browser(interaction, owner_id=42, kind="staff")
        browser = captured["view"]
        assert isinstance(browser, DankGuildResourceBrowserView)
        assert browser.resource_kinds == ("text",)
        assert len(browser.candidates) == 31
        assert browser.page_count == 2
        found = await browser.search("general news")
        assert any(item.resource_id == 900 for item in found.candidates)
        by_id = await browser.search("900")
        assert [item.resource_id for item in by_id.candidates] == [900]
        assert all(item.resource_type == "text" for item in browser.candidates)

        captured.clear()
        await ui._open_voice_safety_channel_browser(interaction, owner_id=42, kind="voice")
        voice_browser = captured["view"]
        assert voice_browser.resource_kinds == ("voice",)
        assert [item.resource_id for item in voice_browser.candidates] == [901]

    asyncio.run(run())


def test_voice_safety_picker_rechecks_private_public_and_bot_permissions(monkeypatch) -> None:
    import stoney_verify.commands_ext.public_voice_safety as ui

    class TextChannel:
        id = 456
        def __init__(self, *, everyone=False, bot_can_send=True, staff_can_view=True):
            self.everyone = everyone
            self.bot_can_send = bot_can_send
            self.staff_can_view = staff_can_view
        def permissions_for(self, member):
            if member == "everyone":
                return SimpleNamespace(view_channel=self.everyone)
            if member == "bot":
                return SimpleNamespace(view_channel=True, send_messages=self.bot_can_send)
            return SimpleNamespace(view_channel=self.staff_can_view)

    class VoiceChannel:
        id = 789
        def permissions_for(self, member):
            return SimpleNamespace(view_channel=True, connect=True)

    monkeypatch.setattr(ui, "_VOICE_SAFETY_CHANNEL_MODES", {
        "voice": ("voice_safety_voice_channel_id", VoiceChannel, "Voice", ("voice",)),
        "staff": ("voice_safety_staff_channel_id", TextChannel, "Staff", ("text",)),
        "notice": ("voice_safety_notice_channel_id", TextChannel, "Notice", ("text",)),
    })
    guild = SimpleNamespace(default_role="everyone", me="bot")
    actor = "staff"
    public = TextChannel(everyone=True)
    private = TextChannel(everyone=False)
    guild.get_channel = lambda channel_id: {456: guild.selected, 789: guild.voice}.get(channel_id)
    guild.voice = VoiceChannel()
    guild.selected = public
    assert "publicly visible" in ui._voice_safety_channel_blocker(guild, public, "staff", actor)
    assert ui._voice_safety_channel_blocker(guild, public, "notice", actor) == ""
    guild.selected = private
    assert "hidden from @everyone" in ui._voice_safety_channel_blocker(guild, private, "notice", actor)
    assert ui._voice_safety_channel_blocker(guild, private, "staff", actor) == ""
    no_send = TextChannel(everyone=False, bot_can_send=False)
    guild.selected = no_send
    assert "Send Messages" in ui._voice_safety_channel_blocker(guild, no_send, "staff", actor)
    blocked_staff = TextChannel(everyone=False, staff_can_view=False)
    guild.selected = blocked_staff
    assert "cannot view" in ui._voice_safety_channel_blocker(guild, blocked_staff, "staff", actor)
    guild.selected = private
    assert ui._voice_safety_channel_blocker(guild, guild.voice, "voice", actor) == ""
    assert "correct channel type" in ui._voice_safety_channel_blocker(guild, private, "voice", actor)


def test_voice_safety_picker_rejects_selection_when_session_starts(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.commands_ext.public_voice_safety as ui

        class Response:
            def __init__(self):
                self.items = []
            def is_done(self):
                return False
            async def edit_message(self, **kwargs):
                self.items.append(("edit", kwargs))
            async def send_message(self, message, *, ephemeral=False):
                self.items.append(("error", message, ephemeral))

        owner = SimpleNamespace(id=42)
        guild = SimpleNamespace(
            id=1,
            channels=[SimpleNamespace(id=100, type=discord.ChannelType.text, name="🎯staff", category=None)],
        )
        response = Response()
        interaction = SimpleNamespace(guild=guild, user=owner, client=object(), response=response)
        active = [False]
        monkeypatch.setattr(ui, "_staff", lambda _: True)
        monkeypatch.setattr(ui, "ensure_community_voice_caption_manager", lambda bot: SimpleNamespace(
            status_for_guild=lambda gid: {"active": active[0], "scope_kind": "voice_safety"},
        ))
        await ui._open_voice_safety_channel_browser(interaction, owner_id=42, kind="staff")
        browser = response.items[0][1]["view"]
        assert browser.candidates
        active[0] = True
        await ui._open_voice_safety_channel_browser(interaction, owner_id=42, kind="staff")
        assert response.items[-1][0] == "error"
        assert "Stop active" in response.items[-1][1]
    asyncio.run(run())


def test_voice_safety_host_checks_explain_missing_flags_without_exposing_key(monkeypatch) -> None:
    from stoney_verify.community_voice_caption_runtime import (
        voice_safety_host_checks, voice_safety_host_enabled,
    )
    from stoney_verify.commands_ext.public_voice_safety import _readiness

    names = (
        "DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED",
        "DANK_VOICE_SAFETY_ENABLED",
        "DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED",
        "DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED",
        "GEMINI_API_KEY",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    status = voice_safety_host_checks()
    assert set(status) == set(names)
    assert all(value is False for value in status.values())
    assert not voice_safety_host_enabled()
    ready, explanation = _readiness(SimpleNamespace(), {})
    assert not ready
    assert all(name in explanation for name in names)

    for name in names[:-1]:
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-secret-do-not-output")
    status = voice_safety_host_checks()
    assert all(status.values())
    assert voice_safety_host_enabled()
    assert "not-a-real-secret" not in str(status)
    monkeypatch.setenv("DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED", "false")
    ready, explanation = _readiness(SimpleNamespace(), {})
    assert not ready
    assert "DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED" in explanation
    assert "DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED" not in explanation


def test_voice_safety_staff_panel_displays_readiness_without_key(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.commands_ext.public_voice_safety as ui

        class FakeGuild:
            id = 51

        async def config(gid, *, refresh=False):
            return {
                "voice_safety_voice_channel_id": 88,
                "voice_safety_staff_channel_id": 89,
                "voice_safety_notice_channel_id": 90,
            }

        bot = object()
        monkeypatch.setattr(ui, "get_guild_config", config)
        monkeypatch.setattr(
            ui, "ensure_community_voice_caption_manager",
            lambda value: SimpleNamespace(
                status_for_guild=lambda gid: {"active": False}
            ),
        )
        monkeypatch.setattr(ui, "_readiness", lambda guild, config: (False, "Host configuration incomplete"))
        monkeypatch.setattr(ui, "voice_safety_host_checks", lambda: {
            "DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED": True,
            "DANK_VOICE_SAFETY_ENABLED": True,
            "DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED": False,
            "DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED": False,
            "GEMINI_API_KEY": True,
        })
        embed = await ui._embed(SimpleNamespace(guild=FakeGuild(), client=bot))
        fields = {field.name: field.value for field in embed.fields}
        assert "Host readiness" in next(k for k in fields if "Host readiness" in k)
        status = next(v for k, v in fields.items() if "Host readiness" in k)
        assert "❌ **Gemini paid-project confirmation**" in status
        assert "❌ **Adult-only usage confirmation**" in status
        assert "✅ **DAVE captions switch**" in status
        assert "Gemini API key configured" in status
        assert "not-a-real-secret" not in str(embed.to_dict())

    asyncio.run(run())


def test_voice_safety_coverage_selects_accessible_two_human_rooms(monkeypatch) -> None:
    import stoney_verify.voice_safety_coverage as coverage

    class FakeVoice:
        def __init__(self, cid, humans, *, bots=0, accessible=True):
            self.id = cid
            self.members = [SimpleNamespace(id=cid * 10 + i, bot=False) for i in range(humans)]
            self.members += [SimpleNamespace(id=cid * 100 + i, bot=True) for i in range(bots)]
            self.accessible = accessible
        def permissions_for(self, member):
            return SimpleNamespace(view_channel=self.accessible, connect=self.accessible)

    monkeypatch.setattr(coverage.discord, "VoiceChannel", FakeVoice)
    rooms = [
        FakeVoice(10, 1, bots=3), FakeVoice(11, 3), FakeVoice(12, 2),
        FakeVoice(13, 4, accessible=False), FakeVoice(14, 2),
    ]
    class Guild:
        me = "bot"
        voice_channels = rooms
        def get_channel(self, cid):
            return next((vc for vc in rooms if vc.id == cid), None)

    guild = Guild()
    cfg = {"voice_safety_voice_channel_id": 12}
    assert coverage.coverage_mode(cfg) == coverage.SINGLE
    assert [v.id for v in coverage.qualifying_channels(guild, cfg)] == [12]
    assert coverage.human_count(rooms[0]) == 1
    cfg[coverage.COVERAGE_KEY] = coverage.ALL
    assert [v.id for v in coverage.qualifying_channels(guild, cfg)] == [11, 12, 14]
    cfg[coverage.COVERAGE_KEY] = "unknown"
    assert coverage.coverage_mode(cfg) == coverage.SINGLE
    assert [v.id for v in coverage.qualifying_channels(guild, cfg)] == [12]


def test_voice_safety_all_vcs_auto_starts_only_qualifying_room(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.community_voice_caption_runtime as runtime
        import stoney_verify.guild_config as guild_config
        import stoney_verify.voice_safety_coverage as coverage

        class FakeVoice:
            def __init__(self, cid, humans):
                self.id = cid
                self.members = [SimpleNamespace(id=cid + x, bot=False) for x in range(humans)]
            def permissions_for(self, member):
                return SimpleNamespace(view_channel=True, connect=True)

        monkeypatch.setattr(runtime.discord, "VoiceChannel", FakeVoice)
        rooms = [FakeVoice(31, 1), FakeVoice(32, 2), FakeVoice(33, 3)]
        guild = SimpleNamespace(
            id=5, me="bot", voice_channels=rooms,
            get_channel=lambda cid: next((v for v in rooms if v.id == cid), None),
        )
        bot = SimpleNamespace(guilds=[guild], get_guild=lambda gid: guild)
        cfg = {"voice_safety_enabled": True, coverage.COVERAGE_KEY: coverage.ALL,
               "voice_safety_staff_channel_id": 40, "voice_safety_notice_channel_id": 41}
        async def read(gid, **kwargs):
            return cfg
        monkeypatch.setattr(guild_config, "get_guild_config", read)
        monkeypatch.setattr(runtime, "voice_safety_host_enabled", lambda: True)
        monkeypatch.setattr(runtime, "live_captions_enabled", lambda: True)
        manager = CommunityVoiceCaptionManager(bot)
        calls = []
        async def begin(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(session_id="voice_safety:5")
        monkeypatch.setattr(manager, "start_voice_safety", begin)
        assert not await manager.maybe_auto_start_voice_safety(guild, joined_voice_id=31)
        assert await manager.maybe_auto_start_voice_safety(guild, joined_voice_id=32)
        assert calls[-1]["voice_channel_id"] == 32
        assert calls[-1]["automatic"] is True
        # Simulate the result being a real owned receiver for contention handling.
        manager._guild_owner[5] = "voice_safety:5"
        assert not await manager.maybe_auto_start_voice_safety(guild, joined_voice_id=33)
        manager._guild_owner.clear()
        await manager.restore_voice_safety_after_ready()
        assert calls[-1]["voice_channel_id"] == 33
        assert len(calls) == 2
    asyncio.run(run())


def test_voice_safety_releases_room_below_two_and_checks_next_room(monkeypatch) -> None:
    async def run() -> None:
        class FakeVoice:
            id = 51
            members = [SimpleNamespace(id=7, bot=False), SimpleNamespace(id=8, bot=False)]
        voice = FakeVoice()
        guild = SimpleNamespace(id=4, get_channel=lambda cid: voice)
        member = SimpleNamespace(id=7, bot=False, guild=guild)
        manager = CommunityVoiceCaptionManager(SimpleNamespace())
        sid = voice_safety_scope_id(4)
        state = SimpleNamespace(
            session_id=sid, scope_kind="voice_safety", voice_channel_id=51,
            safety_automatic=True, safety_consent_epochs={}, safety_consents=set(),
        )
        manager._sessions[sid] = state
        manager._guild_owner[4] = sid
        disabled, stopped, retried = [], [], []

        async def disable(*args):
            disabled.append(args[1])
        async def stop(sid):
            stopped.append(sid)
            manager._guild_owner.pop(4, None)
            manager._sessions.pop(sid, None)
        async def try_next(guild):
            retried.append(guild.id)

        monkeypatch.setattr(manager, "_disable_user_for_state", disable)
        monkeypatch.setattr(manager, "stop", stop)
        monkeypatch.setattr(manager, "maybe_auto_start_voice_safety", try_next)
        before = SimpleNamespace(channel=voice)
        after = SimpleNamespace(channel=None)
        await manager.handle_voice_state_update(member, before, after)
        assert disabled == [7]
        assert stopped == [sid]
        assert retried == [4]
    asyncio.run(run())
