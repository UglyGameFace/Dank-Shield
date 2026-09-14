from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify.setup_engine import verification_modes
from stoney_verify.verification_new import basic_verify


ALLOWED_ID_GUILD = 1357215261001912320


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def guild(guild_id: int = 4242) -> SimpleNamespace:
    return SimpleNamespace(id=guild_id, name=f"Guild {guild_id}")


def test_basic_verify_is_allowed_only_when_simple_verify_is_enabled() -> None:
    basic_cfg = {
        "setup_choice": "basic_verify",
        "verification_enabled": True,
        "basic_verify_enabled": True,
        "basic_button_verify_enabled": True,
    }
    assert verification_modes.basic_verify_allowed_for_guild(guild(), basic_cfg) is True
    assert verification_modes.effective_verification_mode(guild(), basic_cfg) == "basic_button"

    voice_only = {
        "setup_choice": "voice_check",
        "verification_enabled": True,
        "basic_verify_enabled": False,
        "basic_button_verify_enabled": False,
        "voice_verification_enabled": True,
        "verification_allows_voice": True,
    }
    assert verification_modes.basic_verify_allowed_for_guild(guild(), voice_only) is False
    assert verification_modes.effective_verification_mode(guild(), voice_only) == "voice_verify"

    no_verification = {
        "setup_choice": "basic_server",
        "verification_enabled": False,
        "basic_verify_enabled": False,
        "voice_verification_enabled": False,
    }
    assert verification_modes.basic_verify_allowed_for_guild(guild(), no_verification) is False
    assert verification_modes.effective_verification_mode(guild(), no_verification) == "disabled"


def test_custom_simple_plus_voice_keeps_basic_verify_available() -> None:
    cfg = {
        "setup_choice": "custom_setup",
        "verification_enabled": True,
        "basic_verify_enabled": True,
        "basic_button_verify_enabled": True,
        "voice_verification_enabled": True,
        "vc_verify_enabled": True,
    }

    assert verification_modes.basic_verify_allowed_for_guild(guild(), cfg) is True
    assert verification_modes.effective_verification_mode(guild(), cfg) == "basic_button"


def test_legacy_basic_configs_remain_compatible() -> None:
    aggregate_only = {"verification_enabled": True}
    assert verification_modes.basic_verify_allowed_for_guild(guild(), aggregate_only) is True
    assert verification_modes.effective_verification_mode(guild(), aggregate_only) == "basic_button"

    old_mode_only = {"verification_mode": "basic_button"}
    assert verification_modes.basic_verify_allowed_for_guild(guild(), old_mode_only) is True
    assert verification_modes.effective_verification_mode(guild(), old_mode_only) == "basic_button"


def test_explicit_basic_disable_beats_stale_legacy_mode_string() -> None:
    cfg = {
        "verification_mode": "basic_button",
        "basic_verify_enabled": False,
        "basic_button_verify_enabled": False,
        "voice_verification_enabled": True,
    }

    assert verification_modes.basic_verify_allowed_for_guild(guild(), cfg) is False
    assert verification_modes.effective_verification_mode(guild(), cfg) == "voice_verify"


def test_allowlisted_id_mode_has_precedence_over_basic() -> None:
    cfg = {
        "setup_choice": "custom_setup",
        "verification_enabled": True,
        "basic_verify_enabled": True,
        "id_verify_enabled": True,
        "verification_requires_id": True,
    }
    protected = guild(ALLOWED_ID_GUILD)

    assert verification_modes.config_requests_id_verify(cfg) is True
    assert verification_modes.basic_verify_allowed_for_guild(protected, cfg) is False
    assert verification_modes.effective_verification_mode(protected, cfg) == "id_verify"


def test_non_allowlisted_id_request_does_not_silently_downgrade_to_basic() -> None:
    cfg = {
        "setup_choice": "id_check",
        "verification_enabled": True,
        "basic_verify_enabled": False,
        "id_verify_enabled": True,
        "verification_requires_id": True,
    }
    public_guild = guild(999)

    assert verification_modes.config_requests_id_verify(cfg) is True
    assert verification_modes.basic_verify_allowed_for_guild(public_guild, cfg) is False
    assert verification_modes.effective_verification_mode(public_guild, cfg) == "disabled"


def test_non_allowlisted_id_plus_voice_can_still_use_voice_without_basic() -> None:
    cfg = {
        "setup_choice": "id_voice_check",
        "verification_enabled": True,
        "basic_verify_enabled": False,
        "voice_verification_enabled": True,
        "id_verify_enabled": True,
        "verification_requires_id": True,
    }
    public_guild = guild(999)

    assert verification_modes.basic_verify_allowed_for_guild(public_guild, cfg) is False
    assert verification_modes.effective_verification_mode(public_guild, cfg) == "voice_verify"


def test_stale_basic_button_is_blocked_before_role_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_guild = guild()
    fake_member = SimpleNamespace(id=88, guild=fake_guild)
    cfg = {
        "setup_choice": "voice_check",
        "basic_verify_enabled": False,
        "voice_verification_enabled": True,
    }

    async def fake_get_config(_guild_id: int, *, refresh: bool = False) -> dict[str, Any]:
        assert refresh is True
        return cfg

    def role_resolution_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unauthorized Basic Verify reached role resolution")

    monkeypatch.setattr(basic_verify, "get_guild_config", fake_get_config)
    monkeypatch.setattr(basic_verify, "snapshot_from_config", role_resolution_must_not_run)

    ok, message = run(basic_verify.apply_basic_verification(fake_member))

    assert ok is False
    assert "Voice Verify" in message


def test_basic_panel_post_is_blocked_before_message_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_guild = guild()
    cfg = {
        "setup_choice": "basic_server",
        "basic_verify_enabled": False,
        "verification_enabled": False,
    }

    class FakeTextChannel:
        def __init__(self) -> None:
            self.guild = fake_guild

        def history(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("disabled Basic Verify scanned channel history")

        async def send(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("disabled Basic Verify posted a panel")

    async def fake_get_config(_guild_id: int, *, refresh: bool = False) -> dict[str, Any]:
        assert refresh is True
        return cfg

    monkeypatch.setattr(basic_verify.discord, "TextChannel", FakeTextChannel)
    monkeypatch.setattr(basic_verify, "get_guild_config", fake_get_config)

    result = run(basic_verify.post_basic_verify_panel(FakeTextChannel()))
    assert result == "disabled"
