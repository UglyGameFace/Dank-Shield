from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import stoney_verify.community_lookup_service as lookup_service
import stoney_verify.community_quiet_notice_service as quiet_service
import stoney_verify.community_tools_runtime as runtime_module
import stoney_verify.community_tools_service as service
from stoney_verify.commands_ext.public_community_tools import (
    _normalize_native_poll_choices,
    _parse_dice_notation,
)
from stoney_verify.community_quiet_notice_service import QuietNoticeConfig
from stoney_verify.community_tools_runtime import StickyRuntime
from stoney_verify.community_tools_service import (
    CommunityStorageUnavailable,
    InvalidCommunityToolValue,
    StickyConfig,
    StickyPoll,
)


def _sticky(**changes: Any) -> StickyConfig:
    base = StickyConfig(guild_id=1, channel_id=2, content="hello")
    return replace(base, **changes)


def _quiet(**changes: Any) -> QuietNoticeConfig:
    base = QuietNoticeConfig(
        guild_id=1,
        channel_id=20,
        content="quiet",
        last_activity_at=datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc),
    )
    return replace(base, **changes)


def test_failed_sticky_delivery_persistence_rolls_back_new_message_and_keeps_old(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        events: list[str] = []

        class FakeNewMessage:
            id = 222

            async def delete(self) -> None:
                events.append("delete_new")

        class FakeChannel:
            id = 2

            async def fetch_message(self, message_id: int) -> Any:
                events.append(f"fetch_old:{message_id}")
                return SimpleNamespace(delete=lambda: None)

        monkeypatch.setattr(runtime_module.discord, "TextChannel", FakeChannel)
        current = _sticky(last_message_id=111, last_sent_at=datetime.now(timezone.utc))

        async def fake_get_sticky(channel_id: int) -> StickyConfig:
            assert channel_id == 2
            return current

        async def fake_send(channel: Any, config: StickyConfig) -> FakeNewMessage:
            assert config == current
            events.append("send_new")
            return FakeNewMessage()

        async def fake_update(*args: Any, **kwargs: Any) -> None:
            events.append("persist_failed")
            raise CommunityStorageUnavailable("db unavailable")

        monkeypatch.setattr(runtime_module, "get_sticky", fake_get_sticky)
        monkeypatch.setattr(runtime_module, "update_sticky_delivery", fake_update)
        runtime = StickyRuntime(SimpleNamespace())
        runtime._send = fake_send  # type: ignore[method-assign]

        result = await runtime.refresh_channel(FakeChannel(), force=True)
        assert result is None
        assert events == ["send_new", "persist_failed", "delete_new"]
        assert not any(item.startswith("fetch_old") for item in events)

    asyncio.run(scenario())


def test_successful_sticky_replacement_persists_before_old_message_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        events: list[str] = []

        class FakeOldMessage:
            async def delete(self) -> None:
                events.append("delete_old")

        class FakeNewMessage:
            id = 222

            async def delete(self) -> None:
                events.append("delete_new")

        class FakeChannel:
            id = 2

            async def fetch_message(self, message_id: int) -> FakeOldMessage:
                events.append(f"fetch_old:{message_id}")
                return FakeOldMessage()

        monkeypatch.setattr(runtime_module.discord, "TextChannel", FakeChannel)
        current = _sticky(last_message_id=111, last_sent_at=datetime.now(timezone.utc))

        async def fake_get_sticky(channel_id: int) -> StickyConfig:
            return current

        async def fake_send(channel: Any, config: StickyConfig) -> FakeNewMessage:
            events.append("send_new")
            return FakeNewMessage()

        async def fake_update(channel_id: int, *, message_id: int, sent_at: datetime) -> StickyConfig:
            events.append("persist_new")
            return replace(current, last_message_id=message_id, last_sent_at=sent_at)

        monkeypatch.setattr(runtime_module, "get_sticky", fake_get_sticky)
        monkeypatch.setattr(runtime_module, "update_sticky_delivery", fake_update)
        runtime = StickyRuntime(SimpleNamespace())
        runtime._send = fake_send  # type: ignore[method-assign]

        result = await runtime.refresh_channel(FakeChannel(), force=True)
        assert result is not None
        assert events == ["send_new", "persist_new", "fetch_old:111", "delete_old"]

    asyncio.run(scenario())


def test_quiet_activity_worker_persists_latest_burst_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        first = datetime(2026, 9, 5, 10, 1, tzinfo=timezone.utc)
        newest = first + timedelta(seconds=15)
        config = _quiet(last_activity_at=first - timedelta(minutes=5))
        runtime = StickyRuntime(SimpleNamespace())
        runtime._quiet_configs[1] = config
        runtime._guild_last_activity[1] = newest
        captured: dict[str, Any] = {}

        async def fake_record(guild_id: int, *, activity_at: datetime, clear_delivery: bool) -> QuietNoticeConfig:
            captured["guild_id"] = guild_id
            captured["activity_at"] = activity_at
            captured["clear_delivery"] = clear_delivery
            return replace(config, last_activity_at=activity_at)

        monkeypatch.setattr(runtime_module, "record_quiet_activity", fake_record)
        await runtime._persist_quiet_activity(config, first, clear_live=False)
        assert captured == {"guild_id": 1, "activity_at": newest, "clear_delivery": False}
        assert runtime._quiet_last_persisted[1] == newest

    asyncio.run(scenario())


def test_quiet_storage_failure_never_deletes_live_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        observed = datetime(2026, 9, 5, 10, 2, tzinfo=timezone.utc)
        config = _quiet(last_notice_message_id=777, last_notice_sent_at=observed - timedelta(hours=1), auto_clear=True)
        runtime = StickyRuntime(SimpleNamespace())
        runtime._quiet_configs[1] = config
        runtime._guild_last_activity[1] = observed
        deleted = False

        async def fake_record(*args: Any, **kwargs: Any) -> None:
            raise CommunityStorageUnavailable("db unavailable")

        async def fake_delete(*args: Any, **kwargs: Any) -> None:
            nonlocal deleted
            deleted = True

        monkeypatch.setattr(runtime_module, "record_quiet_activity", fake_record)
        runtime.delete_quiet_live_message = fake_delete  # type: ignore[method-assign]
        await runtime._persist_quiet_activity(config, observed, clear_live=True)
        assert deleted is False
        assert runtime._guild_last_activity[1] == observed

    asyncio.run(scenario())


class _PagedResponse:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = data


class _PagedQuery:
    def __init__(self, rows: list[dict[str, Any]], ranges: list[tuple[int, int]]) -> None:
        self.rows = rows
        self.ranges = ranges
        self.start = 0
        self.end = len(rows) - 1

    def select(self, *args: Any, **kwargs: Any) -> "_PagedQuery":
        return self

    def eq(self, *args: Any, **kwargs: Any) -> "_PagedQuery":
        return self

    def order(self, *args: Any, **kwargs: Any) -> "_PagedQuery":
        return self

    def range(self, start: int, end: int) -> "_PagedQuery":
        self.start, self.end = start, end
        self.ranges.append((start, end))
        return self

    def execute(self) -> _PagedResponse:
        return _PagedResponse(self.rows[self.start : self.end + 1])


class _PagedSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.ranges: list[tuple[int, int]] = []

    def table(self, name: str) -> _PagedQuery:
        return _PagedQuery(self.rows, self.ranges)


def test_sticky_list_explicitly_pages_past_postgrest_default_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "guild_id": 1,
            "channel_id": index + 1,
            "enabled": True,
            "content": f"sticky {index}",
            "mode": "plain",
            "interval_seconds": 15,
            "message_threshold": 5,
        }
        for index in range(service.POSTGREST_PAGE_SIZE + 1)
    ]
    fake = _PagedSupabase(rows)
    monkeypatch.setattr(service, "_require_supabase", lambda: fake)
    result = service._list_stickies_sync(enabled_only=True)
    assert len(result) == service.POSTGREST_PAGE_SIZE + 1
    assert fake.ranges == [(0, 499), (500, 999)]


def test_quiet_list_explicitly_pages_past_postgrest_default_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "guild_id": index + 1,
            "channel_id": index + 1000,
            "enabled": True,
            "content": "quiet",
            "inactivity_seconds": 7200,
            "auto_clear": True,
        }
        for index in range(service.POSTGREST_PAGE_SIZE + 1)
    ]
    fake = _PagedSupabase(rows)
    monkeypatch.setattr(quiet_service, "_require_supabase", lambda: fake)
    result = quiet_service._list_sync(enabled_only=True)
    assert len(result) == service.POSTGREST_PAGE_SIZE + 1
    assert fake.ranges == [(0, 499), (500, 999)]


class _RpcResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


class _RpcCall:
    def __init__(self, response: _RpcResponse) -> None:
        self.response = response

    def execute(self) -> _RpcResponse:
        return self.response


class _RpcSupabase:
    def __init__(self, response: _RpcResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _RpcCall:
        self.calls.append((name, params))
        return _RpcCall(self.response)


def test_atomic_sticky_bundle_uses_single_rpc_and_returns_both_states(monkeypatch: pytest.MonkeyPatch) -> None:
    poll = StickyPoll(guild_id=1, channel_id=2, question="Pick", options=("A", "B"), votes={})
    sticky = _sticky(mode="poll", content="Pick")
    response = _RpcResponse(
        {
            "sticky": {
                "guild_id": 1,
                "channel_id": 2,
                "enabled": True,
                "content": "Pick",
                "mode": "poll",
                "interval_seconds": 15,
                "message_threshold": 5,
            },
            "poll": {
                "guild_id": 1,
                "channel_id": 2,
                "question": "Pick",
                "options": ["A", "B"],
                "votes": {},
                "state": "active",
            },
        }
    )
    fake = _RpcSupabase(response)
    monkeypatch.setattr(service, "_require_supabase", lambda: fake)
    saved_sticky, saved_poll = service._save_sticky_bundle_sync(sticky, poll)
    assert saved_sticky.mode == "poll"
    assert saved_poll is not None and saved_poll.options == ("A", "B")
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == service.STICKY_BUNDLE_RPC
    assert fake.calls[0][1]["p_sticky"]["channel_id"] == 2
    assert fake.calls[0][1]["p_poll"]["question"] == "Pick"


def test_atomic_bundle_rejects_mismatched_poll_identity_before_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_require_supabase", lambda: pytest.fail("database should not be called"))
    with pytest.raises(InvalidCommunityToolValue):
        service._save_sticky_bundle_sync(
            _sticky(mode="poll", content="Pick"),
            StickyPoll(guild_id=1, channel_id=999, question="Pick", options=("A", "B"), votes={}),
        )


def test_native_poll_dedupes_after_visible_truncation() -> None:
    prefix = "x" * 55
    with pytest.raises(InvalidCommunityToolValue):
        _normalize_native_poll_choices(f"{prefix}A\n{prefix}B")
    assert _normalize_native_poll_choices("A\nA\nB") == ["A", "B"]


def test_dice_parser_supports_normal_notation_and_enforces_bounds() -> None:
    assert _parse_dice_notation("d20") == (1, 20, 0, "1d20")
    assert _parse_dice_notation("4d8+2") == (4, 8, 2, "4d8+2")
    assert _parse_dice_notation("2d6-1") == (2, 6, -1, "2d6-1")
    for bad in ("51d6", "2d1", "2d1001", "nope"):
        with pytest.raises(InvalidCommunityToolValue):
            _parse_dice_notation(bad)


def test_weather_malformed_provider_payload_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async def fake_json_get(url: str, *, params: dict[str, Any], session: Any = None) -> Any:
            if "geocoding" in url:
                return {"results": [{"name": "Detroit", "latitude": "not-a-number", "longitude": -83.0}]}
            return {}

        monkeypatch.setattr(lookup_service, "_json_get", fake_json_get)
        with pytest.raises(lookup_service.CommunityLookupError):
            await lookup_service.weather_lookup("Detroit")

    asyncio.run(scenario())
