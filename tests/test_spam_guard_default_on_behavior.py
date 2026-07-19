from __future__ import annotations

from copy import deepcopy

import pytest

from stoney_verify import spam_guard
from stoney_verify.spam_guard_defaults import SPAM_GUARD_DEFAULT_ENABLED


@pytest.fixture(autouse=True)
def reset_spam_guard_runtime_state(monkeypatch: pytest.MonkeyPatch):
    spam_guard._RUNTIME_SETTINGS.clear()
    spam_guard._SETTINGS_LAST_DIAG_BY_GUILD.clear()
    spam_guard._SETTINGS_DIAG_THROTTLE.clear()
    monkeypatch.setattr(spam_guard, "_SETTINGS_TABLE_AVAILABLE", None)
    yield
    spam_guard._RUNTIME_SETTINGS.clear()
    spam_guard._SETTINGS_LAST_DIAG_BY_GUILD.clear()
    spam_guard._SETTINGS_DIAG_THROTTLE.clear()


@pytest.mark.asyncio
async def test_brand_new_guild_defaults_enabled_and_bootstraps_row(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted_payloads: list[dict] = []

    monkeypatch.setattr(spam_guard, "_fetch_settings_sync", lambda _gid: ("ok", {}, "row_missing"))

    def fake_upsert(payload):
        persisted_payloads.append(deepcopy(payload))
        return True, "upsert_ok"

    monkeypatch.setattr(spam_guard, "_upsert_settings_sync", fake_upsert)

    settings = await spam_guard.get_spam_settings(101)

    assert SPAM_GUARD_DEFAULT_ENABLED is True
    assert settings["enabled"] is True
    assert persisted_payloads
    assert persisted_payloads[0]["spam_blocker_enabled"] is True
    diag = spam_guard._SETTINGS_LAST_DIAG_BY_GUILD[101]
    assert diag["original_row_found"] is False
    assert diag["persisted"] is True
    assert diag["source"] == "db-bootstrap"


@pytest.mark.asyncio
async def test_missing_db_row_is_default_enabled_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spam_guard, "_fetch_settings_sync", lambda _gid: ("ok", {}, "row_missing"))
    monkeypatch.setattr(spam_guard, "_upsert_settings_sync", lambda _payload: (False, "temporary_write_failure"))

    settings = await spam_guard.get_spam_settings(102)

    assert settings["enabled"] is True
    diag = spam_guard._SETTINGS_LAST_DIAG_BY_GUILD[102]
    assert diag["status"] == "ok"
    assert diag["original_row_found"] is False
    assert diag["source"] == "defaults"
    assert diag["effective_enabled"] is True


@pytest.mark.asyncio
async def test_explicit_persisted_enabled_row_stays_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"guild_id": "103", "spam_blocker_enabled": True, "spam_mode": "timeout"}
    upserts: list[dict] = []
    monkeypatch.setattr(spam_guard, "_fetch_settings_sync", lambda _gid: ("ok", row, "row_found"))
    monkeypatch.setattr(spam_guard, "_upsert_settings_sync", lambda payload: (upserts.append(payload) or True, "upsert_ok"))

    settings = await spam_guard.get_spam_settings(103)

    assert settings["enabled"] is True
    assert upserts == []
    diag = spam_guard._SETTINGS_LAST_DIAG_BY_GUILD[103]
    assert diag["original_row_found"] is True
    assert diag["persisted"] is True
    assert diag["effective_enabled"] is True


@pytest.mark.asyncio
async def test_explicit_persisted_disabled_row_stays_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"guild_id": "104", "spam_blocker_enabled": False, "spam_mode": "timeout"}
    upserts: list[dict] = []
    monkeypatch.setattr(spam_guard, "_fetch_settings_sync", lambda _gid: ("ok", row, "row_found"))
    monkeypatch.setattr(spam_guard, "_upsert_settings_sync", lambda payload: (upserts.append(payload) or True, "upsert_ok"))

    settings = await spam_guard.get_spam_settings(104)

    assert settings["enabled"] is False
    assert upserts == []
    diag = spam_guard._SETTINGS_LAST_DIAG_BY_GUILD[104]
    assert diag["original_row_found"] is True
    assert diag["persisted"] is True
    assert diag["effective_enabled"] is False


@pytest.mark.asyncio
async def test_temporary_database_read_failure_uses_last_known_explicit_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    cached = spam_guard._cache_runtime_settings(
        105,
        {"guild_id": "105", "enabled": False, "mode": "timeout"},
        source="db",
        persisted=True,
    )
    # Force the cache stale so the loader actually attempts the DB read.
    spam_guard._RUNTIME_SETTINGS[105]["__meta_loaded_at"] = 0.0
    monkeypatch.setattr(
        spam_guard,
        "_fetch_settings_sync",
        lambda _gid: ("unavailable", None, "fetch_error:TimeoutError"),
    )

    settings = await spam_guard.get_spam_settings(105)

    assert cached["enabled"] is False
    assert settings["enabled"] is False
    diag = spam_guard._SETTINGS_LAST_DIAG_BY_GUILD[105]
    assert diag["status"] == "unavailable"
    assert diag["source"] == "cache"
    assert diag["effective_enabled"] is False


@pytest.mark.asyncio
async def test_explicit_disabled_choice_survives_restart_via_persisted_row(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = {"guild_id": "106", "spam_blocker_enabled": True, "spam_mode": "timeout"}

    def fake_fetch(_gid):
        return "ok", deepcopy(stored), "row_found"

    def fake_upsert(payload):
        stored.clear()
        stored.update(deepcopy(payload))
        return True, "upsert_ok"

    monkeypatch.setattr(spam_guard, "_fetch_settings_sync", fake_fetch)
    monkeypatch.setattr(spam_guard, "_readback_settings_sync", fake_fetch)
    monkeypatch.setattr(spam_guard, "_upsert_settings_sync", fake_upsert)

    saved, persisted = await spam_guard.save_spam_settings(106, {"enabled": False})
    assert persisted is True
    assert saved["enabled"] is False
    assert stored["spam_blocker_enabled"] is False

    # Simulate a process restart: runtime cache/diagnostics disappear, DB row remains.
    spam_guard._RUNTIME_SETTINGS.clear()
    spam_guard._SETTINGS_LAST_DIAG_BY_GUILD.clear()
    spam_guard._SETTINGS_TABLE_AVAILABLE = None

    reloaded = await spam_guard.get_spam_settings(106)
    assert reloaded["enabled"] is False
    assert spam_guard._SETTINGS_LAST_DIAG_BY_GUILD[106]["persisted"] is True
