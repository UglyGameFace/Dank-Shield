from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
    assert "await _flush_bulk_recovery_stats(gid, reason=reason)" in text


def test_startup_invite_scan_uses_recovery_budget_but_live_scan_does_not(monkeypatch) -> None:
    guild = SimpleNamespace(id=777)
    channel = SimpleNamespace(id=778, guild=guild)
    reservations: list[tuple[int, str]] = []

    async def reserve(weight: int, *, label: str) -> None:
        reservations.append((int(weight), str(label)))

    async def scan(_channel, **kwargs):
        assert kwargs["repost_mixed"] is True
        return {
            "checked": 0,
            "matched": 0,
            "allowed": 0,
            "deleted": 0,
            "failed": 0,
        }

    monkeypatch.setattr(runtime, "reserve_recovery_discord_rest_requests", reserve)
    monkeypatch.setattr(runtime.policy, "scan_channel_invites", scan)

    asyncio.run(
        runtime._scan_channel(
            channel,
            limit=250,
            source="auto-reconcile:ready",
        )
    )
    assert reservations == [(3, "invite history guild=777 channel=778")]

    asyncio.run(
        runtime._scan_channel(
            channel,
            limit=75,
            source="live-recovery:create",
        )
    )
    assert reservations == [(3, "invite history guild=777 channel=778")]


def test_contentless_onebump_still_triggers_live_recovery_sweep() -> None:
    message = SimpleNamespace(
        content="",
        author=SimpleNamespace(
            id=1028956609382199346,
            bot=True,
            name="OneBump",
        ),
        application_id=1028956609382199346,
        application=None,
        interaction_metadata=None,
        embeds=[],
        attachments=[],
        components=[],
        poll=None,
    )

    assert runtime.policy.extract_invite_codes_from_message(message) == []
    assert runtime.policy.is_contentless_trusted_advertiser_candidate(message) is True
    assert runtime._looks_invite_related(message) is True


def test_contentless_onebump_interaction_receipt_does_not_trigger_ad_recovery() -> None:
    message = SimpleNamespace(
        content="",
        author=SimpleNamespace(
            id=1028956609382199346,
            bot=True,
            name="OneBump",
        ),
        application_id=1028956609382199346,
        application=None,
        interaction_metadata=SimpleNamespace(id=123),
        embeds=[],
        attachments=[],
        components=[],
        poll=None,
    )

    assert runtime.policy.is_contentless_trusted_advertiser_candidate(message) is False
    assert runtime._looks_invite_related(message) is False


def test_legacy_invite_runtime_bridge_stays_retired() -> None:
    guard_dir = ROOT / "stoney_verify" / "startup_guards"
    for filename in (
        "invite_live_enforcer_guard.py",
        "discord_invite_blocker_runtime_guard.py",
        "spam_guard_invite_hard_block.py",
    ):
        assert not (guard_dir / filename).exists(), filename

    globals_source = (ROOT / "stoney_verify" / "globals.py").read_text(encoding="utf-8")
    assert "enforce_live_invite_message" in globals_source

    recovery_source = (
        ROOT / "stoney_verify" / "invite_reconciliation_runtime.py"
    ).read_text(encoding="utf-8")
    assert "policy.scan_channel_invites(" in recovery_source


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
    flushes: list[tuple[int, str]] = []

    async def scan(channel, *, limit, repost_mixed, source):
        calls.append((channel.id, limit, source, repost_mixed))
        return {
            "checked": 9,
            "matched": 2,
            "allowed": 1,
            "deleted": 1,
            "failed": 0,
        }

    async def flush(guild_id: int, *, reason: str) -> None:
        flushes.append((guild_id, reason))

    monkeypatch.setattr(runtime, "_guild_reconciliation_enabled", enabled)
    monkeypatch.setattr(runtime.policy, "scan_channel_invites", scan)
    monkeypatch.setattr(runtime, "_flush_bulk_recovery_stats", flush)

    result = asyncio.run(runtime._reconcile_guild(guild, reason="ready", force=True))

    assert calls == [(11, 250, "auto-reconcile:ready", True)]
    assert flushes == [(123, "ready")]
    assert result == {
        "channels": 1,
        "skipped_permission": 1,
        "skipped_inactive": 0,
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
    calls: list[tuple[str, bool, datetime | None, datetime | None]] = []
    after = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    before = datetime(2026, 9, 20, 12, 1, tzinfo=timezone.utc)

    async def window(_guild):
        return after, before

    async def reconcile(_guild, *, reason, force=False, after=None, before=None):
        calls.append((reason, force, after, before))
        if len(calls) == 1:
            return {"deferred": 1}
        return {"deferred": 0}

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(runtime, "_recovery_window", window)
    monkeypatch.setattr(runtime, "_reconcile_guild", reconcile)
    monkeypatch.setattr(runtime, "_sleep", no_sleep)
    runtime._RECONCILE_TASK = None

    asyncio.run(runtime._reconcile_all(bot, reason="ready"))

    assert calls == [
        ("ready", False, after, before),
        ("ready-policy-retry", True, after, before),
    ]


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


def test_reconcile_guild_skips_channel_without_messages_after_checkpoint(monkeypatch) -> None:
    guild = FakeGuild(901)
    channel = guild.text_channels[0]
    runtime._LAST_GUILD_RECONCILE_AT.clear()

    async def enabled(_guild):
        return True

    async def should_not_scan(*_args, **_kwargs):
        raise AssertionError("inactive channel should not hit Discord history")

    monkeypatch.setattr(runtime, "_guild_reconciliation_enabled", enabled)
    monkeypatch.setattr(runtime, "_channel_may_have_messages_after", lambda _channel, _after: False)
    monkeypatch.setattr(runtime.policy, "scan_channel_invites", should_not_scan)

    after = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    before = datetime(2026, 9, 20, 12, 1, tzinfo=timezone.utc)
    result = asyncio.run(
        runtime._reconcile_guild(
            guild,
            reason="ready",
            force=True,
            after=after,
            before=before,
        )
    )

    assert result["channels"] == 0
    assert result["skipped_inactive"] == 1
    assert result["checked"] == 0


def test_scan_channel_forwards_fixed_recovery_window(monkeypatch) -> None:
    guild = FakeGuild(902)
    channel = guild.text_channels[0]
    captured: dict[str, object] = {}

    async def scan(_channel, **kwargs):
        captured.update(kwargs)
        return {"checked": 0, "matched": 0, "allowed": 0, "deleted": 0, "failed": 0}

    monkeypatch.setattr(runtime.policy, "scan_channel_invites", scan)
    after = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    before = datetime(2026, 9, 20, 12, 1, tzinfo=timezone.utc)

    asyncio.run(
        runtime._scan_channel(
            channel,
            limit=250,
            source="auto-reconcile:ready",
            after=after,
            before=before,
        )
    )

    assert captured["after"] == after
    assert captured["before"] == before
    assert captured["limit"] == 250
