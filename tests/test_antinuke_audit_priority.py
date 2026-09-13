from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify.startup_guards import discord_api_safety


def _reset_guard_state() -> None:
    discord_api_safety._AUDIT_LOCKS.clear()
    discord_api_safety._AUDIT_LAST_CALL.clear()
    discord_api_safety._AUDIT_LAST_RATE_LIMIT.clear()


def test_antinuke_search_shape_is_recognized_as_security_priority() -> None:
    assert (
        anti_nuke._AUDIT_SEARCH_LIMIT
        == discord_api_safety._SECURITY_PRIORITY_AUDIT_LIMIT
        == 50
    )

    for action_name in discord_api_safety._SECURITY_PRIORITY_AUDIT_ACTION_NAMES:
        request = {
            "limit": anti_nuke._AUDIT_SEARCH_LIMIT,
            "action": SimpleNamespace(name=action_name),
        }
        assert discord_api_safety._is_security_priority_audit_request(request) is True

    ordinary = {
        "limit": 10,
        "action": SimpleNamespace(name="channel_delete"),
    }
    assert discord_api_safety._is_security_priority_audit_request(ordinary) is False


def test_priority_audit_skips_only_generic_spacing(monkeypatch) -> None:
    _reset_guard_state()
    sleep_calls: list[tuple[int, float]] = []
    forwarded_kwargs: list[dict] = []

    async def fake_original(_guild, *args, **kwargs):
        _ = args
        forwarded_kwargs.append(dict(kwargs))
        yield SimpleNamespace(id=1)

    async def fake_sleep_until_allowed(last_map, key, gap_seconds):
        _ = last_map
        sleep_calls.append((int(key), float(gap_seconds)))

    monkeypatch.setattr(
        discord_api_safety,
        "_ORIGINAL_GUILD_AUDIT_LOGS",
        fake_original,
    )
    monkeypatch.setattr(
        discord_api_safety,
        "_sleep_until_allowed",
        fake_sleep_until_allowed,
    )

    guild = SimpleNamespace(id=12345)

    async def run() -> None:
        priority_action = SimpleNamespace(name="channel_delete")
        async for _entry in discord_api_safety._guarded_audit_logs(
            guild,
            limit=50,
            action=priority_action,
        ):
            pass

        ordinary_action = SimpleNamespace(name="channel_delete")
        async for _entry in discord_api_safety._guarded_audit_logs(
            guild,
            limit=10,
            action=ordinary_action,
        ):
            pass

    asyncio.run(run())

    assert sleep_calls == [(12345, discord_api_safety._audit_cooldown_seconds())]
    assert len(forwarded_kwargs) == 2
    assert all("_dank_priority" not in kwargs for kwargs in forwarded_kwargs)
    _reset_guard_state()


def test_explicit_priority_marker_is_consumed_before_discord_call(monkeypatch) -> None:
    _reset_guard_state()
    sleep_calls: list[tuple[int, float]] = []
    forwarded_kwargs: list[dict] = []

    async def fake_original(_guild, *args, **kwargs):
        _ = args
        forwarded_kwargs.append(dict(kwargs))
        yield SimpleNamespace(id=2)

    async def fake_sleep_until_allowed(last_map, key, gap_seconds):
        _ = last_map
        sleep_calls.append((int(key), float(gap_seconds)))

    monkeypatch.setattr(
        discord_api_safety,
        "_ORIGINAL_GUILD_AUDIT_LOGS",
        fake_original,
    )
    monkeypatch.setattr(
        discord_api_safety,
        "_sleep_until_allowed",
        fake_sleep_until_allowed,
    )

    guild = SimpleNamespace(id=67890)

    async def run() -> None:
        async for _entry in discord_api_safety._guarded_audit_logs(
            guild,
            limit=5,
            action=SimpleNamespace(name="unknown"),
            _dank_priority=True,
        ):
            pass

    asyncio.run(run())

    assert sleep_calls == []
    assert len(forwarded_kwargs) == 1
    assert "_dank_priority" not in forwarded_kwargs[0]
    _reset_guard_state()
