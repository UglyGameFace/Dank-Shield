from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify import setup_service_state as service_state


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_custom_setup_missing_switches_stays_off() -> None:
    state = service_state.service_state_from_config(
        {
            "setup_choice": "custom_setup",
            "setup_choice_label": "Choose My Own Features",
        }
    )

    assert state.tickets is False
    assert state.simple_verify is False
    assert state.voice_verify is False
    assert state.id_verify is False
    assert state.spam_guard is False
    assert state.logs is False
    assert state.any_enabled is False


def test_standard_setup_defaults_match_the_saved_choice() -> None:
    state = service_state.service_state_from_config(
        {"setup_choice": "basic_server"}
    )

    assert state.tickets is True
    assert state.simple_verify is False
    assert state.spam_guard is True
    assert state.logs is True


def test_explicit_saved_switch_overrides_template_default() -> None:
    state = service_state.service_state_from_config(
        {
            "setup_choice": "basic_server",
            "tickets_enabled": False,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        }
    )

    assert state.tickets is False
    assert state.spam_guard is False
    assert state.logs is False


def test_custom_voice_dependencies_are_normalized() -> None:
    patch = service_state.normalize_custom_service_patch(
        {
            "tickets_enabled": False,
            "verification_enabled": False,
            "voice_verification_enabled": True,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        }
    )

    assert patch["voice_verification_enabled"] is True
    assert patch["verification_enabled"] is True
    assert patch["basic_verify_enabled"] is True
    assert patch["tickets_enabled"] is True
    assert patch["moderation_enabled"] is True
    assert patch["setup_completed"] is False


def test_specialized_verification_does_not_fake_simple_verify() -> None:
    state = service_state.service_state_from_config(
        {
            "setup_choice": "id_check",
            "verification_enabled": True,
            "basic_verify_enabled": False,
            "verification_requires_id": True,
        }
    )

    assert state.id_verify is True
    assert state.simple_verify is False
    assert state.verification_enabled is True


def test_completion_is_read_from_the_same_canonical_state() -> None:
    state = service_state.service_state_from_config(
        {
            "setup_choice": "custom_setup",
            "tickets_enabled": True,
            "setup_completed": True,
            "setup_completed_at": "2026-07-21T02:50:02+00:00",
        }
    )

    assert state.completed is True
    assert state.completed_at == "2026-07-21T02:50:02+00:00"


def test_custom_service_save_uses_normalized_aliases_and_invalidates_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_upsert(guild_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        captured["guild_id"] = guild_id
        captured["payload"] = dict(payload)
        return {"guild_id": str(guild_id), **payload}

    monkeypatch.setattr(
        "stoney_verify.commands_ext.public_setup_config_writer.upsert_guild_config",
        fake_upsert,
    )
    monkeypatch.setattr(service_state, "invalidate_guild_config", lambda guild_id: None)

    state = run(
        service_state.save_custom_service_state(
            123,
            {
                "tickets_enabled": False,
                "verification_enabled": True,
                "voice_verification_enabled": False,
                "spam_guard_enabled": False,
                "moderation_enabled": False,
            },
            actor=SimpleNamespace(id=44),
        )
    )

    payload = captured["payload"]
    assert captured["guild_id"] == 123
    assert payload["tickets_enabled"] is False
    assert payload["basic_verify_enabled"] is True
    assert payload["setup_completed"] is False
    assert state.simple_verify is True
    assert state.tickets is False


def test_finish_setup_persists_completion_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_upsert(guild_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        captured["guild_id"] = guild_id
        captured["payload"] = dict(payload)
        return {
            "guild_id": str(guild_id),
            "setup_choice": "custom_setup",
            "tickets_enabled": True,
            **payload,
        }

    monkeypatch.setattr(
        "stoney_verify.commands_ext.public_setup_config_writer.upsert_guild_config",
        fake_upsert,
    )
    monkeypatch.setattr(service_state, "invalidate_guild_config", lambda guild_id: None)

    state = run(
        service_state.mark_setup_completed(
            123,
            actor=SimpleNamespace(id=44),
        )
    )

    assert captured["payload"]["setup_completed"] is True
    assert captured["payload"]["setup_completed_at"]
    assert captured["payload"]["__config_write_source"] == "/dank setup finish"
    assert state.completed is True
