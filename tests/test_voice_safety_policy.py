from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

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
    VOICE_SAFETY_REACTION_ALERT,
    VOICE_SAFETY_REACTION_SILENT,
    VoiceSafetyAdvisor,
    VoiceSafetyVerdict,
    parse_voice_safety_verdict,
    voice_safety_reaction_mode,
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


def test_voice_safety_reaction_mode_fails_safe_to_alerts() -> None:
    assert voice_safety_reaction_mode("alert") == VOICE_SAFETY_REACTION_ALERT
    assert voice_safety_reaction_mode("silent") == VOICE_SAFETY_REACTION_SILENT
    assert voice_safety_reaction_mode(" ALERT ") == VOICE_SAFETY_REACTION_ALERT
    assert voice_safety_reaction_mode(None) == VOICE_SAFETY_REACTION_ALERT
    assert voice_safety_reaction_mode("") == VOICE_SAFETY_REACTION_ALERT
    # Future modes do not exist yet; an unknown value must never silence alerts.
    assert voice_safety_reaction_mode("timeout") == VOICE_SAFETY_REACTION_ALERT
    assert voice_safety_reaction_mode("kick") == VOICE_SAFETY_REACTION_ALERT


def test_voice_safety_context_isolated_by_speaker_with_bounded_cooldown() -> None:
    async def run() -> None:
        seen = []
        clock = [100.0]

        async def classify(text, context=()):
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
        async def classify(text, context=()):
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
        async def classify(text, context=()):
            calls.append(text)
            return VoiceSafetyVerdict("harassment", "review", "Targeted harassment")
        advisor = VoiceSafetyAdvisor(classify=classify)
        for transcript, confidence, admitted in [
            ("[unclear audio]", 0.99, True),
            ("Barely audible and mistaken", 0.3, True),
            ("No consent by this user", 0.99, False),
            ("ok", 1.0, True),
        ]:
            assert await advisor.inspect(3, transcript, confidence, is_admitted=lambda uid: admitted) is None
        assert calls == []
    asyncio.run(run())


def test_voice_safety_short_single_word_reaches_classifier() -> None:
    async def run() -> None:
        calls = []
        async def classify(text, context=()):
            calls.append(text)
            return VoiceSafetyVerdict("harassment", "review", "Targeted harassment")
        advisor = VoiceSafetyAdvisor(classify=classify)
        # A lone word (6 chars) was dropped by the old 12-char floor and never
        # reached the classifier (live case 2026-11-10: a single slur, no alert).
        verdict = await advisor.inspect(3, "target", 1.0, is_admitted=lambda uid: True)
        assert verdict is not None
        assert verdict.category == "harassment"
        assert calls == ["target"]
        assert advisor.requests == 1
    asyncio.run(run())


def test_voice_safety_sends_other_speakers_as_context() -> None:
    async def run() -> None:
        clock = [1_000.0]
        seen = []
        async def classify(text, context=()):
            seen.append((text, tuple(context)))
            return VoiceSafetyVerdict("harassment", "review", "Targeted harassment")
        advisor = VoiceSafetyAdvisor(classify=classify, clock=lambda: clock[0])
        check = lambda uid: True
        # Another speaker sets the scene; their line reaches the model as
        # context when the next speaker is reviewed.
        assert await advisor.inspect(22, "Tell us the story from last night", 0.99, is_admitted=check) is not None
        clock[0] += 10
        verdict = await advisor.inspect(11, "Stop targeting the same member now", 0.99, is_admitted=check)
        assert verdict is not None
        assert seen[-1] == (
            "Stop targeting the same member now",
            ("Tell us the story from last night",),
        )
        # Only the two most recent other-speaker lines are sent, each cut to
        # the endpoint's 300-char cap; the speaker's own earlier lines stay
        # in the snapshot, never in context.
        clock[0] += 10
        assert await advisor.inspect(33, "x" * 400, 0.99, is_admitted=check) is not None
        clock[0] += 10
        assert await advisor.inspect(44, "It was just a story", 0.99, is_admitted=check) is not None
        clock[0] += 10
        verdict = await advisor.inspect(55, "One more targeted remark here", 0.99, is_admitted=check)
        assert verdict is not None
        assert seen[-1] == (
            "One more targeted remark here",
            ("x" * 300, "It was just a story"),
        )
        # Lines older than the 45s window are left out entirely.
        clock[0] += 100
        assert await advisor.inspect(66, "Something said much later", 0.99, is_admitted=check) is not None
        assert seen[-1] == ("Something said much later", ())
    asyncio.run(run())


def test_voice_safety_opt_out_invalidates_in_flight_review() -> None:
    async def run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        async def classify(text, context=()):
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


def test_voice_safety_membership_restores_present_members_without_a_record(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.community_voice_caption_runtime as runtime

        member = SimpleNamespace(
            id=2, voice=SimpleNamespace(channel=SimpleNamespace(id=7)),
        )
        guild = SimpleNamespace(get_member=lambda key: member if key == 2 else None)
        manager = CommunityVoiceCaptionManager(
            SimpleNamespace(get_guild=lambda key: guild)
        )

        class FakeBridge:
            def __init__(self):
                self.opted = set()
            def is_opted_in(self, user_id):
                return int(user_id) in self.opted
            def opted_in_user_ids(self):
                return tuple(self.opted)
            def opt_in(self, user_id):
                self.opted.add(int(user_id))

        class FakeEngine:
            def __init__(self):
                self.transcriber = SimpleNamespace()
                self.allowed = set()
            def allow_user(self, user_id):
                self.allowed.add(int(user_id))

        async def prefs(*args, **kwargs):
            return {"language_code": ""}
        manager.preferences_for_user = prefs

        async def no_decline(*args, **kwargs):
            return False
        monkeypatch.setattr(runtime, "has_voice_safety_decline", no_decline)

        state = SimpleNamespace(
            session_id="voice_safety:1", guild_id=1, voice_channel_id=7,
            scope_kind="voice_safety", bridge=FakeBridge(),
            engine=FakeEngine(), safety_consent_epochs={},
            safety_consents=set(),
        )
        manager._sessions[state.session_id] = state
        channel = SimpleNamespace(members=[SimpleNamespace(id=2, bot=False)])
        # Membership is the opt-in: present with no stored record at all.
        assert await manager._restore_auto_opt_ins(state, channel) == 1
        assert state.safety_consents == {2}
        assert state.bridge.is_opted_in(2)

        # A standing explicit decline is the only block.
        async def declined(*args, **kwargs):
            return True
        monkeypatch.setattr(runtime, "has_voice_safety_decline", declined)
        state2 = SimpleNamespace(
            session_id="voice_safety:2", guild_id=1, voice_channel_id=7,
            scope_kind="voice_safety", bridge=FakeBridge(),
            engine=FakeEngine(), safety_consent_epochs={},
            safety_consents=set(),
        )
        manager._sessions[state2.session_id] = state2
        assert await manager._restore_auto_opt_ins(state2, channel) == 0
        assert state2.safety_consents == set()
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


def test_voice_safety_disclosure_names_local_backend_when_watch(monkeypatch) -> None:
    from stoney_verify.verification_new.basic_verify import build_basic_verify_embed
    from stoney_verify.voice_safety_consent import verification_message_discloses_voice_safety

    guild = SimpleNamespace(get_channel=lambda key: None, text_channels=[])
    monkeypatch.setenv("DANK_VOICE_SAFETY_BACKEND", "watch")
    configured = build_basic_verify_embed(guild, {"voice_safety_enabled": True})
    voice_copy = str(configured.fields[0].value)
    assert "transcribed locally" in voice_copy
    assert "Google Gemini" not in str(configured.to_dict())
    assert "private safety alerts" in voice_copy
    assert "raw audio" in voice_copy
    assert verification_message_discloses_voice_safety(
        SimpleNamespace(embeds=[configured])
    )
    monkeypatch.delenv("DANK_VOICE_SAFETY_BACKEND", raising=False)
    default = build_basic_verify_embed(guild, {"voice_safety_enabled": True})
    assert "Google Gemini" in str(default.fields[0].value)


def test_voice_safety_agreement_record_still_requires_explicit_action(monkeypatch) -> None:
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
    assert {"Monitored Voice Channel", "Private Staff Alerts"} <= {
        child.label for child in staff.children if getattr(child, "label", None)
    }
    assert all(int(getattr(child, "row", 0) or 0) <= 4 for child in staff.children)
    assert {"Start / Stop Review", "Enable / Disable Setup", "Reaction: Alerts / Silent"}.issubset({
        child.label for child in staff.children if getattr(child, "label", None)
    })
    public = VoiceSafetyConsentView()
    assert public.timeout is None
    ended = VoiceSafetyEndedView()
    assert ended.opt_out.disabled is False
    assert {child.custom_id for child in public.children} == {
        "dank:voice_safety:consent_off:v1",
    }
    assert {child.label for child in public.children} == {"Decline & Exit Server"}


def test_voice_safety_reaction_toggle_cycles_alert_and_silent(monkeypatch) -> None:
    async def run() -> None:
        import stoney_verify.commands_ext.public_voice_safety as ui

        class FakeGuild:
            id = 51

        cfg = {
            "voice_safety_voice_channel_id": 88,
            "voice_safety_staff_channel_id": 89,
        }

        async def config(gid, *, refresh=False):
            return cfg

        saved = []

        async def fake_save(interaction, patch):
            saved.append(dict(patch))
            cfg.update(patch)
            return dict(cfg)

        class FakeResponse:
            async def defer(self):
                return None

        class FakeFollowup:
            def __init__(self):
                self.sent = []

            async def send(self, *args, **kwargs):
                self.sent.append((args, kwargs))

        class FakeInteraction:
            def __init__(self):
                self.guild = FakeGuild()
                self.client = object()
                self.response = FakeResponse()
                self.followup = FakeFollowup()
                self.edits = []

            async def edit_original_response(self, **kwargs):
                self.edits.append(kwargs)

        monkeypatch.setattr(ui, "get_guild_config", config)
        monkeypatch.setattr(ui, "_save", fake_save)
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

        view = VoiceSafetyStaffView(1)
        button = next(
            child for child in view.children
            if getattr(child, "label", None) == "Reaction: Alerts / Silent"
        )
        interaction = FakeInteraction()
        await button.callback(interaction)
        assert saved == [{"voice_safety_reaction": "silent"}]
        embed = await ui._embed(SimpleNamespace(guild=FakeGuild(), client=object()))
        fields = {field.name: field.value for field in embed.fields}
        assert "Silent (log only)" in fields["Configuration"]
        await button.callback(interaction)
        assert saved[-1] == {"voice_safety_reaction": "alert"}
        embed = await ui._embed(SimpleNamespace(guild=FakeGuild(), client=object()))
        fields = {field.name: field.value for field in embed.fields}
        assert "Alerts only" in fields["Configuration"]

    asyncio.run(run())


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
        member = SimpleNamespace(
            id=99, bot=False, voice=SimpleNamespace(channel=voice),
        )
        member100 = SimpleNamespace(
            id=100, bot=False, voice=SimpleNamespace(channel=voice),
        )
        voice.members = [member, member100]

        class FakeGuild:
            id = 5
            default_role = "everyone"
            me = "bot"
            def get_member(self, uid):
                return {99: member, 100: member100}.get(uid)
            def get_channel(self, cid):
                return {71: voice, 72: staff}.get(int(cid))
            def get_thread(self, cid):
                return None

        guild = FakeGuild()
        bot = SimpleNamespace(get_guild=lambda gid: guild if gid == 5 else None, get_channel=lambda cid: None)
        cfg = {
            "voice_safety_enabled": True,
            "voice_safety_voice_channel_id": 71,
            "voice_safety_staff_channel_id": 72,
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

        async def classify(text, context=()):
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
        monkeypatch.setattr(runtime, "save_voice_safety_agreement", save)
        manager = CommunityVoiceCaptionManager(bot)
        async def preferences(*args, **kwargs):
            return {"language_code": ""}
        manager.preferences_for_user = preferences

        state = await manager.start_voice_safety(
            guild_id=5, voice_channel_id=71, staff_alert_channel_id=72
        )
        assert state.scope_kind == "voice_safety"
        # Membership is the opt-in: both members present are admitted at start,
        # with no stored agreement record and no click. No public notice is
        # posted anywhere: the only sends are the staff alert below.
        assert set(state.bridge.opted_in_user_ids()) == {99, 100}
        assert staff.sent == []
        await state.engine.publish(99, "unwanted explicit remarks directed at a member", 0.98)
        assert len(staff.sent) == 1
        assert "staff review only" in staff.sent[0][0]
        assert "unwanted_sexual_comments" in staff.sent[0][0]
        assert staff.sent[0][1]["allowed_mentions"].everyone is False
        assert await manager.set_voice_safety_consent(5, 99, False) is False
        assert not state.bridge.is_opted_in(99)
        await state.engine.publish(99, "unwanted explicit remarks directed at a member", 0.98)
        assert len(staff.sent) == 1
        # Silent reaction mode: the verdict is logged, nothing posts to staff.
        cfg["voice_safety_reaction"] = "silent"
        await state.engine.publish(100, "unwanted explicit remarks directed at a member", 0.98)
        assert len(staff.sent) == 1
        await manager.stop(voice_safety_scope_id(5))
        # Stopping posts to the private staff channel only; nothing is posted
        # or edited in any public channel.
        assert len(staff.sent) == 2
        assert "Voice Safety stopped" in staff.sent[1][0]
        assert staff.edits == []
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
        # Stopping never blocks a later automatic start: there is no staff
        # pause anymore, so a fresh attempt starts again right away.
        assert not hasattr(manager, "pause_voice_safety_auto_start")
        assert await manager.maybe_auto_start_voice_safety(guild, joined_voice_id=22) is True
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
        monkeypatch.setattr(manager, "preferences_for_user", prefs)
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
    })
    guild = SimpleNamespace(default_role="everyone", me="bot")
    actor = "staff"
    public = TextChannel(everyone=True)
    private = TextChannel(everyone=False)
    guild.get_channel = lambda channel_id: {456: guild.selected, 789: guild.voice}.get(channel_id)
    guild.voice = VoiceChannel()
    guild.selected = public
    assert "publicly visible" in ui._voice_safety_channel_blocker(guild, public, "staff", actor)
    # The removed "notice" kind is rejected as an unknown channel type.
    assert "correct channel type" in ui._voice_safety_channel_blocker(guild, public, "notice", actor)
    guild.selected = private
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

@pytest.mark.parametrize("persisted", [False, True])
def test_voice_safety_refresh_edits_existing_verify_panel_in_place(monkeypatch, persisted) -> None:
    """The same published message loses and regains the agreement on toggle."""
    import stoney_verify.startup_guards.basic_verify_panel_auto_refresh_guard as guard
    from stoney_verify.verification_new.basic_verify import build_basic_verify_embed

    async def run() -> None:
        cfg = {
            "verify_channel_id": 77,
            "voice_safety_enabled": False,
            "basic_verify_panel_message_id": "901" if persisted else "",
        }

        class FakeMessage:
            id = 901
            author = SimpleNamespace(id=11)

            def __init__(self, guild):
                self.embeds = [build_basic_verify_embed(guild, cfg)]
                self.edits = []

            async def edit(self, *, embed, view):
                self.embeds = [embed]
                self.edits.append((embed, view))

        class FakeChannel:
            id = 77

            def __init__(self):
                self.message = None
                self.fetches = 0
                self.scans = 0

            async def fetch_message(self, message_id):
                self.fetches += 1
                assert message_id == 901
                return self.message

            async def history(self, *, limit):
                self.scans += 1
                assert limit == 100
                yield self.message

        channel = FakeChannel()

        class FakeGuild:
            id = 42
            me = SimpleNamespace(id=11)
            text_channels = []

            def get_channel(self, channel_id):
                return channel if int(channel_id) == 77 else None

        guild = FakeGuild()
        channel.message = FakeMessage(guild)

        async def load_config(_guild):
            return dict(cfg)

        monkeypatch.setattr(guard.discord, "TextChannel", FakeChannel)
        monkeypatch.setattr(guard, "_load_cfg", load_config)
        monkeypatch.setattr(guard, "_can_scan", lambda _: True)
        monkeypatch.setattr(guard, "effective_verification_mode", lambda *_: "basic_button")

        for enabled in (True, False):
            cfg["voice_safety_enabled"] = enabled
            assert await guard.refresh_basic_verify_panel(guild, reason="setting toggled")
            embed, view = channel.message.edits[-1]
            labels = {child.label for child in view.children}
            if enabled:
                assert "Agree & Verify" in labels
                assert "Decline & Exit Server" in labels
                assert "Voice Safety Agreement v1" in str(embed.to_dict())
            else:
                assert labels == {"Verify"}
                assert "Voice Safety Agreement v1" not in str(embed.to_dict())
                assert "Tap **Verify**" in embed.description

        assert len(channel.message.edits) == 2
        assert channel.fetches == (2 if persisted else 0)
        assert channel.scans == (0 if persisted else 2)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("starting_enabled", "panel_refresh_succeeds"),
    [(True, True), (False, True), (True, False), (False, False)],
)
def test_voice_safety_toggle_refreshes_public_verify_and_fails_safe(
    monkeypatch, starting_enabled, panel_refresh_succeeds
) -> None:
    import stoney_verify.commands_ext.public_voice_safety as ui
    import stoney_verify.setup_engine.verification_modes as modes
    import stoney_verify.startup_guards.basic_verify_panel_auto_refresh_guard as guard

    async def run() -> None:
        state = {"voice_safety_enabled": starting_enabled}
        events = []

        async def load_config(_guild_id, *, refresh=False):
            return dict(state)

        async def save(_interaction, patch):
            state.update(patch)
            events.append(("saved", state["voice_safety_enabled"]))
            return dict(state)

        class FakeManager:
            active = starting_enabled
            started = 0

            def status_for_guild(self, _guild_id):
                return {
                    "active": self.active,
                    "scope_kind": "voice_safety" if self.active else None,
                }

            async def stop(self, scope_id):
                assert scope_id == voice_safety_scope_id(42)
                self.active = False
                events.append(("stopped", True))

            async def maybe_auto_start_voice_safety(self, _guild):
                self.started += 1
                events.append(("started", True))

        class Response:
            async def defer(self):
                pass

        class Followup:
            def __init__(self):
                self.messages = []

            async def send(self, message, **kwargs):
                self.messages.append(message)

        class Interaction:
            guild = SimpleNamespace(id=42)
            client = SimpleNamespace()
            response = Response()

            def __init__(self):
                self.followup = Followup()
                self.edits = []

            async def edit_original_response(self, **kwargs):
                self.edits.append(kwargs)

        manager = FakeManager()

        async def refresh(_guild, *, reason):
            events.append(("refreshed", state["voice_safety_enabled"]))
            assert reason == "voice safety setting changed"
            return panel_refresh_succeeds

        async def embed(_interaction):
            return discord.Embed(title="staff panel")

        monkeypatch.setattr(ui, "get_guild_config", load_config)
        monkeypatch.setattr(ui, "_save", save)
        monkeypatch.setattr(ui, "_embed", embed)
        monkeypatch.setattr(ui, "ensure_community_voice_caption_manager", lambda _: manager)
        monkeypatch.setattr(modes, "effective_verification_mode", lambda *_: "basic_button")
        monkeypatch.setattr(guard, "refresh_basic_verify_panel", refresh)

        view = VoiceSafetyStaffView(1)
        toggle = next(child for child in view.children if child.label == "Enable / Disable Setup")
        interaction = Interaction()
        await toggle.callback(interaction)

        assert len(interaction.edits) == 1
        assert events[0] == ("saved", not starting_enabled)
        assert ("refreshed", not starting_enabled) in events

        if starting_enabled:
            assert state["voice_safety_enabled"] is False
            assert ("stopped", True) in events
            assert manager.started == 0
            assert bool(interaction.followup.messages) is not panel_refresh_succeeds
        elif panel_refresh_succeeds:
            assert state["voice_safety_enabled"] is True
            assert manager.started == 1
            assert events.index(("refreshed", True)) < events.index(("started", True))
            assert interaction.followup.messages == []
        else:
            assert state["voice_safety_enabled"] is False
            assert manager.started == 0
            assert events[-1] == ("saved", False)
            assert any("remains OFF" in m for m in interaction.followup.messages)

    asyncio.run(run())
