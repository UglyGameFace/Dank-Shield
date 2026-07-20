from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import spam_guard, spam_guard_startup_state
from stoney_verify.globals import bot
from stoney_verify.startup_guards import spam_guard_default_state_guard as compatibility_guard


def test_spam_guard_startup_state_labels_distinguish_default_persisted_and_error() -> None:
    classify = spam_guard_startup_state.classify_spam_guard_startup_state

    assert (
        classify(
            {"enabled": True},
            {"status": "ok", "original_row_found": False, "persisted": True},
        )
        == "DEFAULT ENABLED"
    )
    assert (
        classify(
            {"enabled": True},
            {"status": "ok", "original_row_found": True, "persisted": True},
        )
        == "PERSISTED ENABLED"
    )
    assert (
        classify(
            {"enabled": False},
            {"status": "ok", "original_row_found": True, "persisted": True},
        )
        == "PERSISTED DISABLED"
    )
    assert (
        classify(
            {"enabled": True},
            {"status": "unavailable", "reason": "fetch_error:TimeoutError"},
        )
        == "DATABASE LOAD ERROR"
    )


def test_legacy_startup_guard_reexports_native_spam_guard_reporter() -> None:
    assert (
        compatibility_guard.classify_spam_guard_startup_state
        is spam_guard_startup_state.classify_spam_guard_startup_state
    )
    assert (
        compatibility_guard.report_spam_guard_states_once
        is spam_guard_startup_state.report_spam_guard_states_once
    )


def test_native_spam_guard_startup_reporter_is_registered_on_live_bot() -> None:
    listeners = list(getattr(bot, "extra_events", {}).get("on_ready", []) or [])
    matches = [
        listener
        for listener in listeners
        if listener is spam_guard_startup_state.spam_guard_default_state_on_ready
    ]

    assert len(matches) == 1


def test_startup_reporter_emits_all_states_once_per_guild(monkeypatch) -> None:
    guilds = [SimpleNamespace(id=201), SimpleNamespace(id=202), SimpleNamespace(id=203), SimpleNamespace(id=204)]
    cases = {
        201: (
            {"enabled": True},
            {"status": "ok", "original_row_found": False, "persisted": True, "reason": "upsert_ok"},
        ),
        202: (
            {"enabled": True},
            {"status": "ok", "original_row_found": True, "persisted": True, "reason": "row_found"},
        ),
        203: (
            {"enabled": False},
            {"status": "ok", "original_row_found": True, "persisted": True, "reason": "row_found"},
        ),
        204: (
            {"enabled": True},
            {"status": "unavailable", "persisted": False, "reason": "fetch_error:TimeoutError"},
        ),
    }
    calls: list[int] = []
    lines: list[str] = []

    async def fake_get_spam_settings(guild_id: int):
        calls.append(guild_id)
        settings, diag = cases[guild_id]
        spam_guard._SETTINGS_LAST_DIAG_BY_GUILD[guild_id] = dict(diag)
        return dict(settings)

    monkeypatch.setattr(spam_guard, "get_spam_settings", fake_get_spam_settings)
    spam_guard_startup_state._LOGGED_GUILDS.clear()
    spam_guard._SETTINGS_LAST_DIAG_BY_GUILD.clear()

    try:
        asyncio.run(spam_guard_startup_state.report_spam_guard_states_once(guilds, emit=lines.append))
        asyncio.run(spam_guard_startup_state.report_spam_guard_states_once(guilds, emit=lines.append))
    finally:
        spam_guard_startup_state._LOGGED_GUILDS.clear()
        spam_guard._SETTINGS_LAST_DIAG_BY_GUILD.clear()

    assert calls == [201, 202, 203, 204]
    assert len(lines) == 4
    rendered = "\n".join(lines)
    assert "guild=201 state=DEFAULT ENABLED" in rendered
    assert "guild=202 state=PERSISTED ENABLED" in rendered
    assert "guild=203 state=PERSISTED DISABLED" in rendered
    assert "guild=204 state=DATABASE LOAD ERROR" in rendered


def test_warm_persisted_cache_keeps_explicit_disabled_startup_label(monkeypatch) -> None:
    guild_id = 205
    lines: list[str] = []

    async def fake_get_spam_settings(_guild_id: int):
        return {"enabled": False}

    monkeypatch.setattr(spam_guard, "get_spam_settings", fake_get_spam_settings)
    spam_guard_startup_state._LOGGED_GUILDS.clear()
    spam_guard._SETTINGS_LAST_DIAG_BY_GUILD.clear()
    spam_guard._RUNTIME_SETTINGS[guild_id] = {
        "guild_id": str(guild_id),
        "enabled": False,
        "__meta_persisted": True,
        "__meta_source": "db",
    }

    try:
        asyncio.run(
            spam_guard_startup_state.report_spam_guard_states_once(
                [SimpleNamespace(id=guild_id)],
                emit=lines.append,
            )
        )
    finally:
        spam_guard_startup_state._LOGGED_GUILDS.clear()
        spam_guard._SETTINGS_LAST_DIAG_BY_GUILD.clear()
        spam_guard._RUNTIME_SETTINGS.pop(guild_id, None)

    assert len(lines) == 1
    assert "state=PERSISTED DISABLED" in lines[0]
    assert "enabled=False" in lines[0]
    assert "persisted=True" in lines[0]
