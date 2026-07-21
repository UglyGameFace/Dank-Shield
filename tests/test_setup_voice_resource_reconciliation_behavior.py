from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import setup_resource_reconcile as reconcile
from stoney_verify.commands_ext import public_setup_config_writer as writer
from stoney_verify.commands_ext import public_setup_fresh_choice as fresh


ROOT = Path(__file__).resolve().parents[1]


class _State:
    tickets = False
    verification = False
    voice = False
    spamguard = False
    moderation = False

    def as_payload(self):
        return {
            "tickets_enabled": False,
            "verification_enabled": False,
            "voice_verification_enabled": False,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        }


@pytest.mark.asyncio
async def test_saved_all_off_custom_state_is_not_resurrected(monkeypatch):
    async def fake_cfg(*_args, **_kwargs):
        return {"setup_service_mode_saved_at": "2026-07-21T00:00:00+00:00"}

    async def should_not_detect(*_args, **_kwargs):
        raise AssertionError("existing-resource detection must not run after explicit save")

    monkeypatch.setattr(fresh.solid, "get_guild_config", fake_cfg)
    monkeypatch.setattr(fresh, "_detect_existing_service_payload", should_not_detect)

    state = _State()
    resolved, message = await fresh._autofill_custom_state_from_existing(
        SimpleNamespace(id=123),
        state,
    )
    assert resolved is state
    assert message == ""


def test_config_clear_payload_removes_stale_mapping_key():
    existing = {
        "guild_id": "1",
        "settings": {
            "vc_verify_channel_id": "123",
            "keep_me": "yes",
        },
        "vc_verify_channel_id": "123",
    }
    result = writer._settings_payload_without_keys(
        existing,
        {"vc_verify_channel_id"},
        {"setup_completed": False},
    )
    assert "vc_verify_channel_id" not in result
    assert result["keep_me"] == "yes"
    assert result["setup_completed"] is False


class _FakeChannel:
    def __init__(self, channel_id: int, name: str, channel_type: discord.ChannelType):
        self.id = channel_id
        self.name = name
        self.type = channel_type
        self.mention = f"<#${channel_id}>"
        self.members = []
        self.deleted = False

    async def delete(self, *, reason: str = ""):
        assert reason
        self.deleted = True

    async def history(self, *, limit: int = 1):
        if False:
            yield None


class _FakeGuild:
    def __init__(self, channels):
        self.id = 999
        self._channels = {channel.id: channel for channel in channels}
        self.me = None

    def get_channel(self, channel_id: int):
        return self._channels.get(int(channel_id))


@pytest.mark.asyncio
async def test_voice_off_removes_proven_managed_defaults_and_clears_mappings(monkeypatch):
    voice = _FakeChannel(101, "🎙️ Voice Verification", discord.ChannelType.voice)
    queue = _FakeChannel(202, "🎙️・vc-verify-queue", discord.ChannelType.text)
    guild = _FakeGuild([voice, queue])
    cfg = {
        "vc_verify_channel_id": "101",
        "vc_verify_queue_channel_id": "202",
        "vc_verify_channel_managed_id": "101",
        "vc_verify_queue_channel_managed_id": "202",
    }

    async def fake_cfg(*_args, **_kwargs):
        return cfg

    cleared = {}

    async def fake_clear(guild_id, keys, **kwargs):
        cleared["guild_id"] = guild_id
        cleared["keys"] = set(keys)
        cleared["kwargs"] = kwargs
        return {}

    monkeypatch.setattr(reconcile, "get_guild_config", fake_cfg)
    monkeypatch.setattr(writer, "clear_guild_config_keys", fake_clear)

    message = await reconcile.reconcile_disabled_voice_verify(guild)

    assert voice.deleted is True
    assert queue.deleted is True
    assert "vc_verify_channel_id" in cleared["keys"]
    assert "vc_verify_queue_channel_id" in cleared["keys"]
    assert "vc_verify_channel_managed_id" in cleared["keys"]
    assert "vc_verify_queue_channel_managed_id" in cleared["keys"]
    assert "Removed Dank Shield's unused Voice Verify voice channel." in message
    assert "Cleared Voice Verify's saved channel mappings." in message


def test_default_builder_records_managed_voice_resource_ids():
    source = (
        ROOT / "stoney_verify/commands_ext/public_setup_defaults.py"
    ).read_text(encoding="utf-8")
    assert 'updates["vc_verify_channel_managed_id"]' in source
    assert 'updates["vc_verify_queue_channel_managed_id"]' in source
    assert "not vc_verify_preexisting" in source
    assert "not vc_queue_preexisting" in source


def test_custom_picker_close_is_red():
    view = fresh.CustomServiceModeView(_State())
    close = next(
        child
        for child in view.children
        if isinstance(child, discord.ui.Button)
        and str(getattr(child, "label", "") or "") == "Close"
    )
    assert close.style == discord.ButtonStyle.danger
