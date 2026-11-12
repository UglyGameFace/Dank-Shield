from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import soundboard_audio as audio
from stoney_verify.commands_ext import public_soundboard_studio as studio
from stoney_verify.navigation_registry import feature_by_key


class FakeAttachment:
    def __init__(self, filename: str = "funny_clip.wav", size: int = 512) -> None:
        self.filename = filename
        self.size = size
        self.reads = 0

    async def read(self) -> bytes:
        self.reads += 1
        return b"a" * self.size


def test_soundboard_name_sanitization_and_length() -> None:
    assert audio.clean_sound_name("hello_world.mp3") == "hello world"
    assert audio.clean_sound_name("abc\x00def.wav") == "abcdef"
    assert audio.validate_sound_name("  Hello  ") == "Hello"
    with pytest.raises(audio.SoundboardInputError):
        audio.validate_sound_name("a")
    with pytest.raises(audio.SoundboardInputError):
        audio.validate_sound_name("x" * 33)


def test_bad_file_is_rejected_before_read() -> None:
    wrong = FakeAttachment("not_sound.txt")
    with pytest.raises(audio.SoundboardInputError):
        asyncio.run(audio.prepare_attachment(wrong))
    assert wrong.reads == 0

    huge = FakeAttachment("big.mp3", audio.MAX_SOURCE_BYTES + 1)
    with pytest.raises(audio.SoundboardInputError):
        asyncio.run(audio.prepare_attachment(huge))
    assert huge.reads == 0


def test_trimming_is_validated_and_processing_is_bounded(monkeypatch) -> None:
    called = []

    async def fake_convert(source: bytes, *, start: float) -> bytes:
        called.append((len(source), start))
        return b"x" * 4096

    monkeypatch.setattr(audio, "_convert_bytes", fake_convert)
    attachment = FakeAttachment("phone_recording.m4a")
    result = asyncio.run(audio.prepare_attachment(attachment, start_seconds=12))
    assert (result.name, result.filename, len(result.data)) == (
        "phone recording", "phone_recording.m4a", 4096
    )
    assert called == [(512, 12.0)]
    with pytest.raises(audio.SoundboardInputError):
        asyncio.run(audio.prepare_attachment(attachment, start_seconds=301))
    with pytest.raises(audio.SoundboardInputError):
        asyncio.run(audio.prepare_attachment(attachment, start_seconds=-1))
    assert attachment.reads == 1


def test_no_network_or_local_file_demuxer_allowed() -> None:
    source = Path(audio.__file__).read_text(encoding="utf-8")
    assert '"-protocol_whitelist", "pipe"' in source
    assert '"-i", "pipe:0"' in source
    assert 'asyncio.wait_for(proc.communicate(input=source), timeout=20)' in source
    assert audio.MAX_CLIP_SECONDS <= 5.2
    assert audio.MAX_SOUND_BYTES == 512 * 1024
    assert audio.MAX_BATCH == 3


def test_user_and_bot_expression_permissions_are_distinct() -> None:
    create = SimpleNamespace(administrator=False, create_expressions=True, manage_expressions=False)
    manage = SimpleNamespace(administrator=False, create_expressions=False, manage_expressions=True)
    interaction = SimpleNamespace(
        user=SimpleNamespace(guild_permissions=create),
        guild=SimpleNamespace(me=SimpleNamespace(guild_permissions=manage)),
    )
    assert studio._allowed(interaction, "create")
    assert not studio._allowed(interaction, "manage")
    assert studio._allowed(interaction, "manage", bot=True)
    assert not studio._allowed(interaction, "create", bot=True)


def test_mobile_ui_uses_existing_upload_and_home_command_contract() -> None:
    from stoney_verify.commands_ext import public_command_surface_v2 as surface

    source = Path(surface.__file__).read_text(encoding="utf-8")
    assert "Soundboard Clip" in source
    assert "stage_soundboard_upload" in source
    assert "trim_start" in source
    assert '"soundboard_studio"' in surface._FEATURE_ROUTE_KEYS
    entry = feature_by_key("soundboard_studio")
    assert entry is not None and entry.category == "voice"
    assert "soundboard" in entry.aliases
    assert any(choice.value == "soundboard_clip" for choice in surface._UPLOAD_CHOICES)


def test_publish_stays_explicit_and_native_with_anti_nuke_audit_reason() -> None:
    source = Path(studio.__file__).read_text(encoding="utf-8")
    assert "create_soundboard_sound(" in source
    assert "reason=f\"Dank Shield Soundboard Studio upload by" in source
    assert "fetch_soundboard_sounds()" in source
    assert "await sound.edit(" in source
    assert "await sound.delete(" in source
    assert "Published" in source
