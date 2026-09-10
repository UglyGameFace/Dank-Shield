from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import invite_reconciliation_runtime as runtime


ROOT = Path(__file__).resolve().parents[1]


class FakeBot:
    def __init__(self) -> None:
        self.extra_events: dict[str, list[object]] = {}

    def add_listener(self, function, event: str) -> None:
        self.extra_events.setdefault(event, []).append(function)


class FakeChannel:
    def __init__(self, guild, channel_id: int, *, allowed: bool = True) -> None:
        self.guild = guild
        self.id = channel_id
        self._allowed = allowed

    def permissions_for(self, _member):
        return SimpleNamespace(
            view_channel=self._allowed,
            read_messages=self._allowed,
            read_message_history=self._allowed,
            manage_messages=self._allowed,
        )


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.me = object()
        self.text_channels = [
            FakeChannel(self, 11, allowed=True),
            FakeChannel(self, 12, allowed=False),
        ]


def test_main_installs_native_invite_reconciliation_before_app_run() -> None:
    text = (ROOT / "main.py").read_text(encoding="utf-8")
    install_at = text.index("    _install_invite_reconciliation_runtime()\n")
    app_at = text.index("from stoney_verify.app import run")
    assert install_at < app_at


def test_runtime_uses_central_scanner_and_has_ready_resume_recovery() -> None:
    text = (ROOT / "stoney_verify" / "invite_reconciliation_runtime.py").read_text(encoding="utf-8")
    assert "policy.scan_channel_invites(" in text
    assert "policy.delete_message_if_allowed" not in text
    assert '("on_ready", _ready_listener)' in text
    assert '("on_resumed", _resumed_listener)' in text
    assert '("on_message", _recovery_message_listener)' in text
    assert '("on_message_edit", _recovery_edit_listener)' in text
    assert "_AUTO_HISTORY_LIMIT = 250" in text
    assert "_EVENT_HISTORY_LIMIT = 75" in text
    assert "_POLICY_RETRY_DELAY_SECONDS = 15.0" in text


def test_legacy_invite_guard_delegates_instead_of_owning_second_runtime() -> None:
    text = (
        ROOT / "stoney_verify" / "startup_guards" / "discord_invite_blocker_runtime_guard.py"
    ).read_text(encoding="utf-8")
    assert "_SWEEP_TASKS" not in text
    assert "_LAST_SWEEP_AT" not in text
    assert "bot.add_listener" not in text
    assert "recovery.install_invite_reconciliation(bot)" in text
    assert "await recovery._sweep_channel(" in text
    assert "async def _enforce_message(" in text


def test_install_is_idempotent() -> None:
    bot = FakeBot()

    assert runtime.install_invite_reconciliation(bot) is True
    assert runtime.install_invite_reconciliation(bot) is True

    assert len(bot.extra_events["on_message"]) == 1
    assert len(bot.extra_events["on_message_edit"]) == 1
    assert len(bot.extra_events["on_ready"]) == 1
    assert len(bot.extra_events["on_resumed"]) == 1


def test_reconcile_guild_scans_only_channels_with_required_permissions(monkeypatch) -> None:
    guild = FakeGuild(123)
    runtime._LAST_GUILD_RECONCILE_AT.clear()

    async def enabled(_guild):
        return True

    calls: list[tuple[int, int, str, bool]] = []

    async def scan(channel, *, limit, repost_mixed, source):
        calls.append((channel.id, limit, source, repost_mixed))
        return {
            "checked": 9,
            "matched": 2,
            "allowed": 1,
            "deleted": 1,
            "failed": 0,
        }

    monkeypatch.setattr(runtime, "_guild_reconciliation_enabled", enabled)
    monkeypatch.setattr(runtime.policy, "scan_channel_invites", scan)

    result = asyncio.run(runtime._reconcile_guild(guild, reason="ready", force=True))

    assert calls == [(11, 250, "auto-reconcile:ready", True)]
    assert result == {
        "channels": 1,
        "skipped_permission": 1,
        "checked": 9,
        "matched": 2,
        "allowed": 1,
        "deleted": 1,
        "failed": 0,
        "warnings": 0,
        "deferred": 0,
    }


def test_reconcile_guild_counts_scan_warnings_even_without_failed_count(monkeypatch) -> None:
    guild = FakeGuild(124)
    guild.text_channels = [FakeChannel(guild, 21, allowed=True)]
    runtime._LAST_GUILD_RECONCILE_AT.clear()

    async def enabled(_guild):
        return True

    async def scan(_channel, *, limit, repost_mixed, source):
        assert limit == 250
        assert repost_mixed is True
        assert source == "auto-reconcile:ready"
        return {
            "checked": 0,
            "matched": 0,
            "allowed": 0,
            "deleted": 0,
            "failed": 0,
            "warning": "history fetch failed",
        }

    monkeypatch.setattr(runtime, "_guild_reconciliation_enabled", enabled)
    monkeypatch.setattr(runtime.policy, "scan_channel_invites", scan)

    result = asyncio.run(runtime._reconcile_guild(guild, reason="ready", force=True))

    assert result["failed"] == 0
    assert result["warnings"] == 1


def test_reconcile_guild_bounds_created_channel_work(monkeypatch) -> None:
    guild = FakeGuild(790)
    guild.text_channels = [FakeChannel(guild, channel_id, allowed=True) for channel_id in range(20, 25)]
    runtime._LAST_GUILD_RECONCILE_AT.clear()

    async def enabled(_guild):
        return True

    active = 0
    max_active = 0
    calls: list[int] = []

    async def scan(channel, *, limit, repost_mixed, source):
        nonlocal active, max_active
        assert limit == 250
        assert repost_mixed is True
        assert source == "auto-reconcile:ready"
        active += 1
        max_active = max(max_active, active)
        calls.append(channel.id)
        await asyncio.sleep(0)
        active -= 1
        return {
            "checked": 1,
            "matched": 0,
            "allowed": 0,
            "deleted": 0,
            "failed": 0,
        }

    monkeypatch.setattr(runtime, "_guild_reconciliation_enabled", enabled)
    monkeypatch.setattr(runtime.policy, "scan_channel_invites", scan)

    result = asyncio.run(runtime._reconcile_guild(guild, reason="ready", force=True))

    assert sorted(calls) == [20, 21, 22, 23, 24]
    assert max_active <= runtime._RECONCILE_CONCURRENCY
    assert result["channels"] == 5
    assert result["checked"] == 5
    assert result["warnings"] == 0


def test_reconcile_skips_history_when_no_delete_feature_is_enabled(monkeypatch) -> None:
    guild = FakeGuild(456)
    runtime._LAST_GUILD_RECONCILE_AT.clear()

    async def disabled(_guild):
        return False

    async def should_not_scan(*args, **kwargs):
        raise AssertionError("history scan should not run")

    monkeypatch.setattr(runtime, "_guild_reconciliation_enabled", disabled)
    monkeypatch.setattr(runtime.policy, "scan_channel_invites", should_not_scan)

    result = asyncio.run(runtime._reconcile_guild(guild, reason="ready", force=True))

    assert result["channels"] == 0
    assert result["checked"] == 0
    assert result["deferred"] == 0
    assert 456 in runtime._LAST_GUILD_RECONCILE_AT


def test_unavailable_policy_is_deferred_without_starting_cooldown(monkeypatch) -> None:
    guild = FakeGuild(654)
    runtime._LAST_GUILD_RECONCILE_AT.clear()

    async def unavailable(_guild):
        return None

    async def should_not_scan(*args, **kwargs):
        raise AssertionError("history scan should wait for policy recovery")

    monkeypatch.setattr(runtime, "_guild_reconciliation_enabled", unavailable)
    monkeypatch.setattr(runtime.policy, "scan_channel_invites", should_not_scan)

    result = asyncio.run(runtime._reconcile_guild(guild, reason="ready", force=True))

    assert result["deferred"] == 1
    assert result["checked"] == 0
    assert 654 not in runtime._LAST_GUILD_RECONCILE_AT


def test_empty_policy_load_shape_is_treated_as_unavailable(monkeypatch) -> None:
    guild = FakeGuild(655)

    async def empty_policy(_guild, *, refresh=False):
        assert refresh is True
        return None, {}

    monkeypatch.setattr(runtime.policy, "load_invite_policy", empty_policy)

    assert asyncio.run(runtime._guild_reconciliation_enabled(guild)) is None


def test_unavailable_guild_config_source_is_treated_as_unavailable(monkeypatch) -> None:
    guild = FakeGuild(657)

    async def unavailable_policy(_guild, *, refresh=False):
        assert refresh is True
        return {"source": "unavailable:db_read_failed"}, {"invite_shield_enabled": True}

    monkeypatch.setattr(runtime.policy, "load_invite_policy", unavailable_policy)

    assert asyncio.run(runtime._guild_reconciliation_enabled(guild)) is None


def test_reconcile_all_retries_policy_unavailable_guild_once(monkeypatch) -> None:
    guild = FakeGuild(656)
    bot = SimpleNamespace(guilds=[guild])
    calls: list[tuple[str, bool]] = []

    async def reconcile(_guild, *, reason, force=False):
        calls.append((reason, force))
        if len(calls) == 1:
            return {"deferred": 1}
        return {"deferred": 0}

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(runtime, "_reconcile_guild", reconcile)
    monkeypatch.setattr(runtime, "_sleep", no_sleep)
    runtime._RECONCILE_TASK = None

    asyncio.run(runtime._reconcile_all(bot, reason="ready"))

    assert calls == [("ready", False), ("ready-policy-retry", True)]


def test_event_recovery_rescans_recent_channel_history(monkeypatch, capsys) -> None:
    guild = FakeGuild(789)
    channel = guild.text_channels[0]
    runtime._LAST_CHANNEL_SWEEP_AT.clear()
    runtime._CHANNEL_SWEEP_TASKS.clear()

    async def enabled(_guild):
        return True

    calls: list[tuple[int, int, str]] = []

    async def scan(ch, *, limit, repost_mixed, source):
        calls.append((ch.id, limit, source))
        return {
            "checked": 7,
            "matched": 2,
            "allowed": 1,
            "deleted": 1,
            "failed": 0,
            "warning": None,
        }

    monkeypatch.setattr(runtime, "_guild_reconciliation_enabled", enabled)
    monkeypatch.setattr(runtime.policy, "scan_channel_invites", scan)

    asyncio.run(runtime._sweep_channel(channel, reason="create"))

    assert calls == [(11, 75, "live-recovery:create")]
    output = capsys.readouterr().out
    assert "matched=2 allowed=1 deleted=1 failed=0" in output
