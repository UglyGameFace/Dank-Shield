from __future__ import annotations

import asyncio

from stoney_verify import spam_guard
from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
from stoney_verify.spam_guard_defaults import SPAM_GUARD_DEFAULT_ENABLED
from stoney_verify.startup_guards import setup_service_modes as modes


def test_authoritative_spam_guard_enabled_policy_is_on() -> None:
    assert SPAM_GUARD_DEFAULT_ENABLED is True
    assert spam_guard._default_settings(301)["enabled"] is True
    assert modes._default_spam_settings(301)["enabled"] is True


def test_setup_spam_settings_preserve_explicit_disabled_choice() -> None:
    normalized = modes._normalize_spam_settings(302, {"enabled": False})

    assert normalized["enabled"] is False


def test_service_state_defaults_spamguard_and_moderation_on(monkeypatch) -> None:
    async def no_config(_guild_id: int, refresh: bool = True):
        return None

    monkeypatch.setattr(modes, "get_guild_config", no_config)

    state = asyncio.run(modes.load_service_state(303))

    assert state.spamguard is True
    assert state.moderation is True
    assert state.source == "defaults"


def test_service_state_preserves_explicit_disabled_config(monkeypatch) -> None:
    async def load_config(_guild_id: int, refresh: bool = True):
        return {
            "tickets_enabled": True,
            "verification_enabled": False,
            "voice_verification_enabled": False,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
            "source": "guild_configs",
        }

    monkeypatch.setattr(modes, "get_guild_config", load_config)

    state = asyncio.run(modes.load_service_state(304))

    assert state.spamguard is False
    assert state.moderation is False
    assert state.source == "guild_configs"


def test_normal_plain_setup_choices_select_spamguard_by_default() -> None:
    normal_choice_keys = (
        "basic_server",
        "basic_verify",
        "help_desk",
        "voice_check",
        "id_check",
        "id_voice_check",
    )

    for key in normal_choice_keys:
        choice = fresh.get_plain_setup_choice(key)
        assert choice is not None
        flags = fresh._service_flags_for_choice(choice)
        assert flags["spam_guard_enabled"] is True
        assert flags["moderation_enabled"] is True


def test_custom_setup_can_deliberately_leave_spamguard_unselected() -> None:
    choice = fresh.get_plain_setup_choice("custom_setup")

    assert choice is not None
    flags = fresh._service_flags_for_choice(choice)
    assert flags["spam_guard_enabled"] is False
