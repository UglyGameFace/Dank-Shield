from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import somewhere_420_runtime as runtime
from stoney_verify import somewhere_420_schedules as schedules
from stoney_verify.commands_ext import public_somewhere_420 as board
from stoney_verify.commands_ext import public_somewhere_420_settings as settings


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_daily_timezone_math_survives_spring_and_fall_dst() -> None:
    # 4:20 New York is 20:20 UTC in summer, 21:20 UTC in winter.
    assert schedules.next_daily_fire("America/New_York", now=utc("2026-07-01T12:00:00Z")) == utc("2026-07-01T20:20:00Z")
    assert schedules.next_daily_fire("America/New_York", now=utc("2026-01-01T12:00:00Z")) == utc("2026-01-01T21:20:00Z")
    assert schedules.next_daily_fire("America/New_York", now=utc("2026-03-07T22:00:00Z")) == utc("2026-03-08T20:20:00Z")
    assert schedules.next_daily_fire("America/New_York", now=utc("2026-10-31T22:00:00Z")) == utc("2026-11-01T21:20:00Z")
    assert schedules.next_daily_fire("Asia/Kathmandu", now=utc("2026-04-01T10:00:00Z")) == utc("2026-04-01T10:35:00Z")


def test_timezone_validation_and_quiet_hours() -> None:
    with pytest.raises(ValueError):
        schedules.valid_timezone("")
    with pytest.raises(ValueError):
        schedules.valid_timezone("Moon/Nowhere")
    for hour in (22, 23, 0, 7):
        assert schedules.quiet_at(hour, 22, 8)
    assert not schedules.quiet_at(16, 22, 8)
    assert not schedules.quiet_at(16, 0, 0)
    assert schedules.quiet_at(11, 9, 17)


def test_saved_preferences_are_disabled_by_default_and_prevalidated(monkeypatch) -> None:
    saved = []

    async def store(label, operation):
        class Response:
            def __init__(self):
                self.data = [dict(saved[-1])] if saved else []
        # The closure expects DB table.upsert(...).execute().
        class Table:
            def upsert(self, payload, on_conflict):
                saved.append(dict(payload))
                return self
            def execute(self):
                return Response()
        class DB:
            def table(self, name):
                assert name == schedules.TABLE
                return Table()
        return operation(DB())
    monkeypatch.setattr(schedules, "_execute", store)

    result = asyncio.run(schedules.save_schedule("member", 12, zone_name="America/New_York", enabled=False, favorite_city="Tokyo"))
    assert result["id"] == "u:12"
    assert result["enabled"] is False
    assert result["notify_role"] is False
    assert result["user_id"] == "12"
    with pytest.raises(ValueError):
        asyncio.run(schedules.save_schedule("member", 12, zone_name="Invalid/Zone", enabled=True))
    with pytest.raises(ValueError):
        asyncio.run(schedules.save_schedule("guild", 77, zone_name="UTC", enabled=True))
    with pytest.raises(ValueError):
        asyncio.run(schedules.save_schedule("member", 77, zone_name="UTC", enabled=False, favorite_city="Randomland"))


def test_bounded_claims_use_single_atomic_rpc(monkeypatch) -> None:
    calls = []
    async def pretend(label, callback):
        class RPC:
            def execute(self):
                return SimpleNamespace(data=[{"kind": "guild", "guild_id": "123"}])
        class DB:
            def rpc(self, name, payload):
                calls.append((name, payload))
                return RPC()
        return callback(DB())
    monkeypatch.setattr(schedules, "_execute", pretend)
    claimed = asyncio.run(schedules.claim_due(limit=999))
    assert claimed == [{"kind": "guild", "guild_id": "123"}]
    assert calls == [(schedules.CLAIM_RPC, {"p_limit": 25})]


def test_disabled_claim_never_sends(monkeypatch) -> None:
    async def get(*a, **kw):
        return {"enabled": False}
    monkeypatch.setattr(runtime, "get_schedule", get)
    bot = SimpleNamespace(get_guild=lambda _id: (_ for _ in ()).throw(AssertionError("must not contact Discord")))
    result = asyncio.run(runtime.deliver_claim(bot, {"kind": "guild", "guild_id": "123"}))
    assert result == "disabled"


def test_scheduled_sql_is_private_indexed_and_once_only() -> None:
    root = Path(__file__).resolve().parents[1]
    a = (root / "supabase/migrations/20261022182000_dank_somewhere_420_schedules.sql").read_text()
    b = (root / "supabase_fresh/migrations/202610200026_dankshield_somewhere_420.sql").read_text()
    assert a == b
    assert "for update skip locked" in a.lower()
    assert "last_local_date" in a
    assert "next_fire_at" in a
    assert "where enabled = true" in a.lower()
    assert "revoke all" in a.lower()
    assert "grant execute" in a.lower()
    assert "security invoker" in a.lower()
    assert "security definer" not in a.lower()


def test_user_and_server_settings_require_explicit_enable() -> None:
    assert settings._truth("ON") is True
    assert settings._truth("off") is False
    with pytest.raises(ValueError):
        settings._truth("yes")
    assert "somewhere_420" in board.open_somewhere_420.__module__
    assert runtime.MAX_BATCH <= 25
    assert runtime.MAX_PARALLEL <= 8


def test_public_toke_command_unchanged_and_quick_uses_internal_owner() -> None:
    import inspect
    from stoney_verify.commands_ext.public_toke import open_toke_command, open_toke_quick_command
    public_parameters = inspect.signature(open_toke_command).parameters
    assert "quick_text_only" not in public_parameters
    assert "message" in public_parameters
    assert "voice" in public_parameters
    assert inspect.iscoroutinefunction(open_toke_quick_command)


def test_worker_is_not_a_per_guild_scan() -> None:
    import inspect
    source = inspect.getsource(runtime._loop)
    assert "claim_due" in source
    assert "for guild in bot.guilds" not in source
    assert "get_schedule" not in source  # only due rows load their own updated configs
