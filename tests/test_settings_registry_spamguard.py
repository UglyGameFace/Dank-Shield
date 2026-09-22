from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import settings_registry as registry
from stoney_verify import spam_guard
from stoney_verify.commands_ext import public_protection_center as protection
from stoney_verify.startup_guards import setup_service_modes as modes


ROOT = Path(__file__).resolve().parents[1]


def test_spam_guard_registry_declares_defaults_bounds_and_storage_aliases() -> None:
    expected = {
        registry.SPAM_GUARD_ENABLED_KEY: ("bool", True, None, None),
        registry.SPAM_GUARD_MODE_KEY: ("choice", "timeout", None, None),
        registry.SPAM_GUARD_APPLY_VERIFIED_KEY: ("bool", True, None, None),
        registry.SPAM_GUARD_WINDOW_SECONDS_KEY: ("int", 12, 5, 60),
        registry.SPAM_GUARD_MESSAGE_THRESHOLD_KEY: ("int", 5, 3, 20),
        registry.SPAM_GUARD_DUPLICATE_THRESHOLD_KEY: ("int", 3, 2, 12),
        registry.SPAM_GUARD_INVITE_THRESHOLD_KEY: ("int", 2, 1, 12),
        registry.SPAM_GUARD_MULTI_INVITE_IMMEDIATE_KEY: ("int", 2, 2, 8),
        registry.SPAM_GUARD_DELETE_HISTORY_KEY: ("int", 8, 1, 30),
        registry.SPAM_GUARD_TIMEOUT_MINUTES_KEY: ("int", 30, 1, 1440),
        registry.SPAM_GUARD_COOLDOWN_SECONDS_KEY: ("int", 20, 5, 300),
    }

    for key, (kind, default, minimum, maximum) in expected.items():
        spec = registry.setting_spec(key)
        assert spec.kind == kind
        assert spec.default == default
        assert spec.owner == "spam_guard"
        assert spec.persistence == "guild_security_settings"
        assert spec.minimum == minimum
        assert spec.maximum == maximum
        assert spec.aliases_before_canonical is True


def test_spam_guard_defaults_are_canonical_and_shared() -> None:
    defaults = registry.spam_guard_defaults(5001)

    assert defaults == spam_guard._default_settings(5001)
    assert defaults["enabled"] is True
    assert defaults["mode"] == "timeout"
    assert defaults["window_seconds"] == 12
    assert defaults["message_threshold"] == 5
    assert defaults["duplicate_threshold"] == 3
    assert defaults["invite_threshold"] == 2
    assert defaults["multi_invite_immediate"] == 2
    assert defaults["delete_history"] == 8
    assert defaults["timeout_minutes"] == 30
    assert defaults["cooldown_seconds"] == 20

    setup_defaults = modes._default_spam_settings(5001)
    assert setup_defaults == defaults
    assert "delete_limit" not in setup_defaults


def test_prefixed_persisted_spam_columns_keep_precedence() -> None:
    normalized = registry.normalize_spam_guard_settings(
        5002,
        {
            "spam_blocker_enabled": False,
            "enabled": True,
            "spam_mode": "kick",
            "mode": "timeout",
            "spam_window_seconds": 9,
            "window_seconds": 45,
            "spam_message_threshold": 7,
            "message_threshold": 18,
            "spam_timeout_minutes": 90,
            "timeout_minutes": 5,
        },
    )

    assert normalized["enabled"] is False
    assert normalized["mode"] == "kick"
    assert normalized["window_seconds"] == 9
    assert normalized["message_threshold"] == 7
    assert normalized["timeout_minutes"] == 90


def test_spam_guard_numeric_bounds_match_runtime_contract() -> None:
    normalized = registry.normalize_spam_guard_settings(
        5003,
        {
            "window_seconds": 999,
            "message_threshold": 1,
            "duplicate_threshold": 999,
            "invite_threshold": 0,
            "multi_invite_immediate": 1,
            "delete_history": 999,
            "timeout_minutes": 99999,
            "cooldown_seconds": 0,
        },
    )

    assert normalized["window_seconds"] == 60
    assert normalized["message_threshold"] == 3
    assert normalized["duplicate_threshold"] == 12
    assert normalized["invite_threshold"] == 1
    assert normalized["multi_invite_immediate"] == 2
    assert normalized["delete_history"] == 30
    assert normalized["timeout_minutes"] == 1440
    assert normalized["cooldown_seconds"] == 5


def test_allowed_invite_code_legacy_shape_is_preserved() -> None:
    stored_list = registry.normalize_spam_guard_settings(
        5004,
        {"spam_allowed_invite_codes": ["ABC", "https://discord.gg/XYZ"]},
    )
    pasted_text = registry.normalize_spam_guard_settings(
        5004,
        {"allowed_invite_codes": "ABC https://discord.gg/XYZ"},
    )

    assert stored_list["allowed_invite_codes"] == [
        "abc",
        "https://discord.gg/xyz",
    ]
    assert pasted_text["allowed_invite_codes"] == ["abc", "xyz"]


def test_spam_guard_module_delegates_normalization_to_registry() -> None:
    row = {
        "spam_blocker_enabled": False,
        "spam_mode": "delete_only",
        "spam_window_seconds": 8,
        "spam_allowed_channel_ids": ["111", "222"],
    }

    assert spam_guard._normalize_settings(5005, row) == registry.normalize_spam_guard_settings(5005, row)


def test_protection_center_uses_registry_owned_spam_presets() -> None:
    assert protection.SPAM_PRESETS is registry.SPAM_GUARD_PRESETS
    assert protection.SPAM_PRESETS["safe"]["timeout_minutes"] == 30
    assert protection.SPAM_PRESETS["strict"]["timeout_minutes"] == 60


def test_setup_compat_module_no_longer_owns_spam_persistence_or_private_cache() -> None:
    source = (
        ROOT
        / "stoney_verify"
        / "startup_guards"
        / "setup_service_modes.py"
    ).read_text(encoding="utf-8")

    assert 'sb.table("guild_security_settings")' not in source
    assert "def _persist_spam_settings" not in source
    assert "def _cache_spam_settings" not in source
    assert "def _spam_settings_payload" not in source
    assert "_fast_settings_for_ui" not in source
    assert "_RUNTIME_SETTINGS" not in source
    assert '"save_spam_settings"' in source
    assert '"get_spam_settings"' in source


def test_setup_reads_canonical_spam_settings(monkeypatch) -> None:
    store = registry.spam_guard_defaults(5006)
    store["enabled"] = False
    store["timeout_minutes"] = 45

    class FakeSpam:
        async def get_spam_settings(self, guild_id: int):
            assert guild_id == 5006
            return dict(store)

        def _build_persistence_label(self, guild_id: int) -> str:
            assert guild_id == 5006
            return "DB-backed"

    monkeypatch.setattr(modes, "_spam_guard_module", lambda: FakeSpam())

    loaded = asyncio.run(modes._load_spam_settings(5006))

    assert loaded["enabled"] is False
    assert loaded["timeout_minutes"] == 45


def test_setup_saves_through_canonical_spam_service(monkeypatch) -> None:
    store = registry.spam_guard_defaults(5007)
    calls: list[dict] = []

    class FakeSpam:
        async def get_spam_settings(self, guild_id: int):
            assert guild_id == 5007
            return dict(store)

        async def save_spam_settings(self, guild_id: int, patch: dict, *, updated_by=None):
            assert guild_id == 5007
            calls.append(dict(patch))
            store.update(dict(patch))
            return registry.normalize_spam_guard_settings(guild_id, store), True

        def _build_persistence_label(self, guild_id: int) -> str:
            assert guild_id == 5007
            return "DB-backed"

    async def fake_service_state(_guild_id: int):
        return modes.ServiceState(
            tickets=False,
            verification=False,
            voice=False,
            spamguard=True,
            moderation=True,
            source="test",
        )

    monkeypatch.setattr(modes, "_spam_guard_module", lambda: FakeSpam())
    monkeypatch.setattr(modes, "load_service_state", fake_service_state)

    state, note = asyncio.run(
        modes._save_spam_actual_settings(
            5007,
            {"enabled": False, "timeout_minutes": 75},
        )
    )

    assert calls == [{"enabled": False, "timeout_minutes": 75}]
    assert state.guard_active is False
    assert state.timeout_minutes == 75
    assert state.persisted is True
    assert "canonical service" in note
    assert "DB-backed" in note


def test_setup_actual_state_uses_canonical_thirty_minute_default(monkeypatch) -> None:
    class FakeSpam:
        async def get_spam_settings(self, guild_id: int):
            return registry.spam_guard_defaults(guild_id)

        def _build_persistence_label(self, guild_id: int) -> str:
            return "DB-backed"

    async def fake_service_state(_guild_id: int):
        return modes.ServiceState(
            tickets=False,
            verification=False,
            voice=False,
            spamguard=True,
            moderation=True,
            source="test",
        )

    monkeypatch.setattr(modes, "_spam_guard_module", lambda: FakeSpam())
    monkeypatch.setattr(modes, "load_service_state", fake_service_state)

    state = asyncio.run(modes._load_spam_actual_state(5008))
    assert state.timeout_minutes == 30
