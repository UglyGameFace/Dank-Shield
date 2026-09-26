from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from stoney_verify.community_voice_caption_runtime import (
    live_captions_enabled,
    live_captions_start_allowed,
    server_caption_scope_id,
)
from stoney_verify.commands_ext.public_command_surface_v2 import CompactDankHomeView
from stoney_verify.commands_ext.public_live_captions import (
    CAPTION_ALLOWED_VOICE_CATEGORIES_KEY,
    CAPTION_ALLOWED_VOICE_CHANNELS_KEY,
    CAPTION_EXCLUDED_VOICE_CHANNELS_KEY,
    CAPTION_VOICE_SCOPE_KEY,
    ServerLiveCaptionsSetupView,
    _bot_owner_authorized,
    _soak_pipeline_diagnosis,
    _voice_allowed_by_config,
)
from stoney_verify.commands_ext.public_setup_start import DankSetupView
from stoney_verify.ui.picker import DankChannelSelect


ROOT = Path(__file__).resolve().parents[1]
SURFACE = ROOT / "stoney_verify" / "commands_ext" / "public_command_surface_v2.py"
GENERAL_UI = ROOT / "stoney_verify" / "commands_ext" / "public_live_captions.py"
RUNTIME = ROOT / "stoney_verify" / "community_voice_caption_runtime.py"
CAPTIONS = ROOT / "stoney_verify" / "community_voice_captions.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _labels(view) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if getattr(item, "label", None)
    }


def test_general_live_captions_are_first_class_on_dank_home() -> None:
    assert "Live Captions" in _labels(CompactDankHomeView(1))
    source = _text(SURFACE)
    assert 'custom_id="dank:home:live_captions:v1"' in source
    assert "open_server_live_captions" in source
    assert "📝 Live Captions" in source


def test_general_live_caption_setup_is_reachable_and_supports_existing_server_vcs() -> None:
    setup_labels = _labels(ServerLiveCaptionsSetupView(1))
    assert {
        "Select Output Channel",
        "Create #live-captions",
        "Toggle All / Selected",
        "Add Allowed VC",
        "Add Voice Category",
        "Exclude VC",
    }.issubset(setup_labels)
    assert "Live Captions" in _labels(DankSetupView(has_missing=True))


def test_voice_scope_rules_keep_unrelated_vcs_out() -> None:
    voice = SimpleNamespace(id=101, category_id=501)
    other_voice = SimpleNamespace(id=202, category_id=502)

    allowed, _ = _voice_allowed_by_config(voice, {CAPTION_VOICE_SCOPE_KEY: "all"})
    assert allowed is True

    allowed, reason = _voice_allowed_by_config(
        voice,
        {
            CAPTION_VOICE_SCOPE_KEY: "all",
            CAPTION_EXCLUDED_VOICE_CHANNELS_KEY: ["101"],
        },
    )
    assert allowed is False
    assert "excluded" in reason.lower()

    selected = {
        CAPTION_VOICE_SCOPE_KEY: "selected",
        CAPTION_ALLOWED_VOICE_CHANNELS_KEY: ["101"],
        CAPTION_ALLOWED_VOICE_CATEGORIES_KEY: [],
    }
    assert _voice_allowed_by_config(voice, selected)[0] is True
    assert _voice_allowed_by_config(other_voice, selected)[0] is False

    by_category = {
        CAPTION_VOICE_SCOPE_KEY: "selected",
        CAPTION_ALLOWED_VOICE_CHANNELS_KEY: [],
        CAPTION_ALLOWED_VOICE_CATEGORIES_KEY: ["501"],
    }
    assert _voice_allowed_by_config(voice, by_category)[0] is True
    assert _voice_allowed_by_config(other_voice, by_category)[0] is False





def test_live_captions_channel_picker_resolves_discord_partial_channel() -> None:
    real_channel = object.__new__(discord.TextChannel)
    partial_channel = SimpleNamespace(id=321, resolve=lambda: real_channel)
    picked: list[object] = []

    async def on_pick(_interaction, channel) -> None:
        picked.append(channel)

    select = DankChannelSelect(
        author_id=7,
        on_pick=on_pick,
        placeholder="Choose caption output",
        channel_types=[discord.ChannelType.text],
    )
    select._values = [partial_channel]
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=7),
        guild=None,
    )

    asyncio.run(select.callback(interaction))

    assert picked == [real_channel]


def test_validation_lock_only_allows_explicit_general_server_soak(monkeypatch) -> None:
    monkeypatch.delenv("DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED", raising=False)

    assert live_captions_start_allowed(scope_kind="server", soak_test=False) is False
    assert live_captions_start_allowed(scope_kind="server", soak_test=True) is True
    assert live_captions_start_allowed(scope_kind="community_hub", soak_test=True) is False


def test_soak_bypass_requires_actual_bot_owner_authorization() -> None:
    class FakeClient:
        async def is_owner(self, user) -> bool:
            return int(user.id) == 7

    owner_interaction = SimpleNamespace(
        client=FakeClient(),
        user=SimpleNamespace(id=7),
    )
    other_interaction = SimpleNamespace(
        client=FakeClient(),
        user=SimpleNamespace(id=8),
    )

    assert asyncio.run(_bot_owner_authorized(owner_interaction)) is True
    assert asyncio.run(_bot_owner_authorized(other_interaction)) is False


def test_soak_pipeline_diagnosis_separates_receive_from_provider_failures() -> None:
    no_frames = {
        "health": {"raw_udp_packets": 0, "frames_seen": 0, "frames_routed": 0},
        "receive_connection": {
            "reader_listening": True,
            "dave_session_present": True,
            "dave_session_ready": True,
            "mapped_ssrcs": 2,
        },
        "segment_failures": 0,
    }
    assert "No UDP voice packets" in _soak_pipeline_diagnosis(no_frames)

    stopped_reader = {
        "health": {
            "raw_udp_packets": 141,
            "frames_seen": 0,
            "frames_routed": 0,
            "opus_decode_drops": 1,
            "reader_failures": 1,
        },
        "receive_connection": {
            "reader_listening": False,
            "reader_error": "OpusError: corrupted stream",
            "dave_session_present": True,
            "dave_session_ready": True,
            "mapped_ssrcs": 6,
        },
        "segment_failures": 0,
    }
    stopped_text = _soak_pipeline_diagnosis(stopped_reader)
    assert "reader stopped after an error" in stopped_text
    assert "OpusError: corrupted stream" in stopped_text

    dave_not_ready = {
        "health": {"raw_udp_packets": 20, "frames_seen": 0, "frames_routed": 0},
        "receive_connection": {
            "reader_listening": True,
            "dave_session_present": True,
            "dave_session_ready": False,
            "mapped_ssrcs": 2,
        },
        "segment_failures": 0,
    }
    assert "DAVE session is not ready" in _soak_pipeline_diagnosis(dave_not_ready)

    pre_sink_drop = {
        "health": {"raw_udp_packets": 20, "frames_seen": 0, "frames_routed": 0},
        "receive_connection": {
            "reader_listening": True,
            "dave_session_present": True,
            "dave_session_ready": True,
            "mapped_ssrcs": 2,
        },
        "segment_failures": 0,
    }
    assert "no PCM reached" in _soak_pipeline_diagnosis(pre_sink_drop)

    routed = {
        "health": {"raw_udp_packets": 20, "frames_seen": 20, "frames_routed": 20},
        "receive_connection": {
            "reader_listening": True,
            "dave_session_present": True,
            "dave_session_ready": True,
            "mapped_ssrcs": 2,
        },
        "segments_transcribed": 0,
        "segment_failures": 0,
    }
    assert "speaker routing are working" in _soak_pipeline_diagnosis(routed)

    provider_failure = {
        "health": {"frames_seen": 20, "frames_routed": 20},
        "segment_failures": 1,
        "last_failure": "Gemini free-tier quota or rate limit was reached (HTTP 429 RESOURCE_EXHAUSTED).",
    }
    rendered = _soak_pipeline_diagnosis(provider_failure)
    assert "Transcription/processing failure" in rendered
    assert "HTTP 429" in rendered

    provider_blocked = {
        "health": {"frames_seen": 20, "frames_routed": 20},
        "provider_blocked_reason": "Gemini free-tier quota or rate limit was reached (HTTP 429 RESOURCE_EXHAUSTED).",
        "provider_blocked_code": "RESOURCE_EXHAUSTED",
        "segment_failures": 1,
    }
    blocked_text = _soak_pipeline_diagnosis(provider_blocked)
    assert "Transcription provider is blocked" in blocked_text
    assert "RESOURCE_EXHAUSTED" in blocked_text

    historical_consent = {
        "health": {"frames_seen": 65, "frames_routed": 0, "frames_not_consented": 65},
        "receive_connection": {
            "reader_listening": True,
            "dave_session_present": True,
            "dave_session_ready": True,
            "mapped_ssrcs": 4,
        },
        "opted_in_user_ids": [123],
        "segment_failures": 0,
    }
    consent_text = _soak_pipeline_diagnosis(historical_consent)
    assert "cumulative" in consent_text
    assert "before opt-in" in consent_text


def test_general_live_captions_reuse_single_hardened_receiver_owner() -> None:
    ui = _text(GENERAL_UI)
    runtime = _text(RUNTIME)

    assert "ensure_community_voice_caption_manager" in ui
    assert "manager.start_server(" in ui
    assert "manager.status_for_guild(" in ui
    assert "server_caption_scope_id" in ui

    # The general UI must not grow a second receive/transcription implementation.
    assert "CaptionEngine" not in ui
    assert "PerSpeakerFrameBridge" not in ui
    assert "connect_receive_client" not in ui

    assert "self._guild_owner" in runtime
    assert "Another Live Captions session in this server already owns the voice receiver." in runtime
    assert '"caption_scope": "server"' in runtime


def test_general_caption_scope_is_memory_only_and_collision_resistant() -> None:
    assert server_caption_scope_id(123456789) == "server:123456789"
    ui = _text(GENERAL_UI)
    assert "supabase" not in ui.casefold()
    assert "community_hub_service" not in ui
    assert "database" not in ui.casefold()


def test_general_live_captions_require_staff_to_start_but_self_consent_only() -> None:
    ui = _text(GENERAL_UI)

    assert "interaction_is_actual_guild_owner" in ui
    assert "interaction_has_administrator_authority" in ui
    assert "interaction_has_manage_guild_authority" in ui
    assert "Only the server owner, an administrator, or someone with Manage Server" in ui

    assert 'label="Caption My Voice"' in ui
    assert "int(interaction.user.id)" in ui
    assert "target_voice_id" in ui
    assert "Join <#{target_voice_id}> before opting your voice" in ui
    assert "Nobody is transcribed automatically." in ui


def test_general_live_captions_keep_privacy_and_physical_source_limits_visible() -> None:
    ui = _text(GENERAL_UI)

    assert "Discord speakers stay isolated before transcription." in ui
    assert "Opting out immediately blocks new audio" in ui
    assert "Google Gemini's transcription API" in ui
    assert "Dank Shield itself does not save it." in ui
    assert "microphone already captures a TV, game audio, or another person" in ui


def test_opt_out_contract_purges_buffered_audio_before_returning() -> None:
    runtime = _text(RUNTIME)
    captions = _text(CAPTIONS)

    opt_out = runtime.index("state.bridge.opt_out(uid)")
    purge = runtime.index("await state.engine.revoke_user(uid)")
    assert opt_out < purge

    assert "self._blocked_user_ids.add(uid)" in captions
    assert "self.segmenter.discard_user(uid)" in captions
    assert "task.cancel()" in captions
    assert "int(frame.user_id) in self._blocked_user_ids" in captions


def test_soak_gate_remains_default_off(monkeypatch) -> None:
    monkeypatch.delenv("DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED", raising=False)
    assert live_captions_enabled() is False
