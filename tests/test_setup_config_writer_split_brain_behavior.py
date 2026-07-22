from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from stoney_verify.commands_ext import public_setup_config_writer as writer


class _FakeQuery:
    def __init__(self, captured: list[dict[str, Any]]) -> None:
        self.captured = captured
        self.payload: dict[str, Any] = {}

    def update(self, payload: dict[str, Any]):
        self.payload = dict(payload)
        self.captured.append(dict(payload))
        return self

    def upsert(self, payload: dict[str, Any], **_kwargs: Any):
        self.payload = dict(payload)
        self.captured.append(dict(payload))
        return self

    def eq(self, *_args: Any):
        return self

    def execute(self):
        return SimpleNamespace(data=[dict(self.payload)])


class _FakeSupabase:
    def __init__(self, captured: list[dict[str, Any]]) -> None:
        self.captured = captured

    def table(self, _name: str) -> _FakeQuery:
        return _FakeQuery(self.captured)


def _split_brain_row() -> dict[str, Any]:
    return {
        "guild_id": "1514374173517152418",
        "voice_verification_enabled": False,
        "vc_verify_enabled": False,
        "voice_verify_enabled": False,
        "verification_allows_voice": False,
        "vc_verify_channel_id": "111",
        "vc_verify_queue_channel_id": "222",
        "settings": {
            "voice_verification_enabled": False,
            "vc_verify_enabled": False,
            "voice_verify_enabled": False,
            "verification_allows_voice": False,
            "vc_verify_channel_id": "111",
            "vc_verify_queue_channel_id": "222",
        },
        "config": {
            "voice_verification_enabled": True,
            "vc_verify_enabled": True,
            "voice_verify_enabled": True,
            "verification_allows_voice": True,
            "vc_verify_channel_id": "111",
            "vc_verify_queue_channel_id": "222",
        },
    }


def test_settings_merge_matches_runtime_flat_column_precedence() -> None:
    merged = writer._settings_payload_update(_split_brain_row(), {})

    assert merged["voice_verification_enabled"] is False
    assert merged["vc_verify_enabled"] is False
    assert merged["voice_verify_enabled"] is False
    assert merged["verification_allows_voice"] is False


def test_owner_setup_write_updates_both_json_columns_atomically(
    monkeypatch,
) -> None:
    existing = _split_brain_row()
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(writer, "get_supabase", lambda: _FakeSupabase(captured))
    monkeypatch.setattr(
        writer,
        "_fetch_existing_config_row_sync",
        lambda _guild_id: dict(existing),
    )

    writer.upsert_guild_config_sync(
        1514374173517152418,
        {
            "voice_verification_enabled": False,
            "vc_verify_enabled": False,
            "voice_verify_enabled": False,
            "verification_allows_voice": False,
            "__config_write_mode": "setup_builder",
            "__config_write_source": "/dank setup feature picker",
        },
    )

    assert len(captured) == 1
    payload = captured[0]
    assert payload["voice_verification_enabled"] is False
    assert payload["settings"]["voice_verification_enabled"] is False
    assert payload["config"]["voice_verification_enabled"] is False
    assert payload["settings"]["verification_allows_voice"] is False
    assert payload["config"]["verification_allows_voice"] is False


def test_channel_mapping_clear_cannot_resurrect_voice_from_stale_json(
    monkeypatch,
) -> None:
    existing = _split_brain_row()
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(writer, "get_supabase", lambda: _FakeSupabase(captured))
    monkeypatch.setattr(
        writer,
        "_fetch_existing_config_row_sync",
        lambda _guild_id: dict(existing),
    )

    writer.clear_guild_config_keys_sync(
        1514374173517152418,
        {"vc_verify_channel_id", "vc_verify_queue_channel_id"},
        source="/dank setup resource reconciliation",
    )

    assert len(captured) == 1
    payload = captured[0]
    assert payload["vc_verify_channel_id"] is None
    assert payload["vc_verify_queue_channel_id"] is None
    assert "vc_verify_channel_id" not in payload["settings"]
    assert "vc_verify_queue_channel_id" not in payload["settings"]
    assert "vc_verify_channel_id" not in payload["config"]
    assert "vc_verify_queue_channel_id" not in payload["config"]
    assert payload["settings"]["voice_verification_enabled"] is False
    assert payload["config"]["voice_verification_enabled"] is False
    assert payload["settings"]["verification_allows_voice"] is False
    assert payload["config"]["verification_allows_voice"] is False
