from __future__ import annotations

from pathlib import Path

from stoney_verify.community_voice_caption_runtime import (
    live_captions_enabled,
    server_caption_scope_id,
)
from stoney_verify.commands_ext.public_command_surface_v2 import CompactDankHomeView


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
    assert "OpenAI's transcription API" in ui
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
