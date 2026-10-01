from __future__ import annotations

import asyncio
import time

from stoney_verify.commands_ext import public_protection_center as protection


def test_protection_panel_timeout_setting_is_runtime_configurable(monkeypatch) -> None:
    monkeypatch.setenv("DANK_PROTECTION_PANEL_LOAD_TIMEOUT_SECONDS", "7.5")
    assert protection._protection_panel_load_timeout_seconds() == 7.5

    monkeypatch.setenv("DANK_PROTECTION_PANEL_LOAD_TIMEOUT_SECONDS", "999")
    assert protection._protection_panel_load_timeout_seconds() == 15.0

    monkeypatch.setenv("DANK_PROTECTION_PANEL_LOAD_TIMEOUT_SECONDS", "bad")
    assert protection._protection_panel_load_timeout_seconds() == 4.0


def test_protection_state_loader_returns_without_waiting_forever(monkeypatch) -> None:
    async def slow_config(*args, **kwargs):
        await asyncio.sleep(0.25)
        return {"automod_enabled": True}

    async def slow_spam(*args, **kwargs):
        await asyncio.sleep(0.25)
        return {"enabled": True, "mode": "timeout"}, "loaded"

    monkeypatch.setattr(protection, "get_guild_config", slow_config)
    monkeypatch.setattr(protection, "_load_spam_settings", slow_spam)

    started = time.monotonic()
    cfg, spam, source, warnings, failures = asyncio.run(
        protection._load_protection_panel_state(123, timeout_seconds=0.01)
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert cfg == {}
    assert spam == {"enabled": False, "mode": "unknown"}
    assert source == "unavailable:timeout"
    assert len(warnings) == 2
    assert {stage for stage, _exc in failures} == {
        "protection_config_refresh_failed",
        "protection_config_cache_failed",
        "protection_spam_load_failed",
    }


def test_protection_state_loader_uses_cached_config_fallback(monkeypatch) -> None:
    calls: list[bool] = []

    async def config_loader(_guild_id: int, *, refresh: bool = False):
        calls.append(bool(refresh))
        if refresh:
            await asyncio.sleep(0.1)
        return {"automod_enabled": True, "source": "cache"}

    async def spam_loader(_guild_id: int):
        return {"enabled": True, "mode": "timeout"}, "loaded"

    monkeypatch.setattr(protection, "get_guild_config", config_loader)
    monkeypatch.setattr(protection, "_load_spam_settings", spam_loader)

    cfg, spam, source, warnings, failures = asyncio.run(
        protection._load_protection_panel_state(456, timeout_seconds=0.01)
    )

    assert calls == [True, False]
    assert cfg["automod_enabled"] is True
    assert spam["enabled"] is True
    assert source == "loaded"
    assert len(warnings) == 1
    assert "cached state" in warnings[0]
    assert {stage for stage, _exc in failures} == {
        "protection_config_refresh_failed",
    }


def test_unavailable_spam_source_forces_degraded_state(monkeypatch) -> None:
    async def config_loader(_guild_id: int, *, refresh: bool = False):
        return {"automod_enabled": True, "source": "db"}

    async def spam_loader(_guild_id: int):
        return {"enabled": False, "mode": "unknown"}, "unavailable:RuntimeError"

    monkeypatch.setattr(protection, "get_guild_config", config_loader)
    monkeypatch.setattr(protection, "_load_spam_settings", spam_loader)

    _cfg, _spam, source, warnings, failures = asyncio.run(
        protection._load_protection_panel_state(789, timeout_seconds=0.05)
    )

    assert source == "unavailable:RuntimeError"
    assert warnings == [
        "Spam Guard state is temporarily unavailable. Settings controls are locked until live state loads."
    ]
    assert failures == []


def test_degraded_protection_view_disables_mutations() -> None:
    view = protection.ProtectionCenterView(
        author_id=1,
        cfg={},
        spam={},
        degraded=True,
    )

    enabled_ids = {
        str(getattr(child, "custom_id", "") or "")
        for child in view.children
        if not bool(getattr(child, "disabled", False))
    }
    assert enabled_ids == {
        "dank_protection:refresh",
        "dank_protection:close",
    }

    refresh = next(
        child
        for child in view.children
        if str(getattr(child, "custom_id", "") or "") == "dank_protection:refresh"
    )
    assert str(getattr(refresh, "label", "")) == "Retry Live State"
