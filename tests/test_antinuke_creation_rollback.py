from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_guardian_runtime as guardian


class FakeChannel:
    def __init__(self, channel_id: int = 5001, *, fail: bool = False) -> None:
        self.id = channel_id
        self.name = "raid-room"
        self.fail = fail
        self.deleted = False
        self.reason = None

    async def delete(self, *, reason=None) -> None:
        if self.fail:
            raise RuntimeError("delete failed")
        self.deleted = True
        self.reason = reason


class FakeWebhook:
    def __init__(self, webhook_id: int = 6001) -> None:
        self.id = webhook_id
        self.name = "raid-hook"
        self.deleted = False
        self.reason = None

    async def delete(self, *, reason=None) -> None:
        self.deleted = True
        self.reason = reason


class FakeGuild:
    def __init__(self, *, webhooks=None) -> None:
        self.id = 4101
        self.owner_id = 999999
        self._webhooks = list(webhooks or [])

    async def webhooks(self):
        return list(self._webhooks)

    def get_member(self, _user_id: int):
        return None

    async def fetch_member(self, _user_id: int):
        raise LookupError


class FakeEntry:
    def __init__(self, entry_id: int, action_name: str, guild, actor, target) -> None:
        self.id = entry_id
        self.action = SimpleNamespace(name=action_name)
        self.guild = guild
        self.user = actor
        self.user_id = actor.id
        self.target = target


def _actor(user_id: int = 7001):
    return SimpleNamespace(id=user_id, roles=[], mention=f"<@{user_id}>")


def _settings(**patch):
    return anti_nuke.normalize_antinuke_settings(
        {"antinuke_enabled": True, "antinuke_mode": "contain", **patch}
    )


def _reset() -> None:
    guardian._CREATION_ROLLBACK_DONE.clear()
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    anti_nuke._AUDIT_CLAIM_LOCKS.clear()


def test_undelegated_channel_create_is_deleted(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor()
    channel = FakeChannel()
    entry = FakeEntry(8001, "channel_create", guild, actor, channel)

    async def fake_settings(_guild_id):
        return _settings()

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    result = asyncio.run(
        guardian._rollback_untrusted_creation(guild, entry, actor, "channel_create")
    )

    assert "deleted unauthorized" in result
    assert channel.deleted is True
    assert "AntiNuke rollback" in str(channel.reason)
    _reset()


def test_undelegated_webhook_create_resolves_id_and_is_deleted(monkeypatch) -> None:
    _reset()
    webhook = FakeWebhook(6002)
    guild = FakeGuild(webhooks=[webhook])
    actor = _actor()
    audit_target = SimpleNamespace(id=webhook.id, name="raid-hook")
    entry = FakeEntry(8002, "webhook_create", guild, actor, audit_target)

    async def fake_settings(_guild_id):
        return _settings()

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    result = asyncio.run(
        guardian._rollback_untrusted_creation(guild, entry, actor, "webhook_create")
    )

    assert "deleted unauthorized" in result
    assert webhook.deleted is True
    assert "AntiNuke rollback" in str(webhook.reason)
    _reset()


def test_delegated_operator_creation_is_not_rolled_back(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(7002)
    channel = FakeChannel()
    entry = FakeEntry(8003, "channel_create", guild, actor, channel)

    async def fake_settings(_guild_id):
        return _settings(antinuke_trusted_user_ids=[actor.id])

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    result = asyncio.run(
        guardian._rollback_untrusted_creation(guild, entry, actor, "channel_create")
    )

    assert result == ""
    assert channel.deleted is False
    _reset()


def test_alert_mode_never_deletes_created_resource(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(7003)
    channel = FakeChannel()
    entry = FakeEntry(8004, "channel_create", guild, actor, channel)

    async def fake_settings(_guild_id):
        return anti_nuke.normalize_antinuke_settings(
            {"antinuke_enabled": True, "antinuke_mode": "alert"}
        )

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    result = asyncio.run(
        guardian._rollback_untrusted_creation(guild, entry, actor, "channel_create")
    )

    assert result == ""
    assert channel.deleted is False
    _reset()


def test_gateway_cleanup_runs_even_if_native_path_already_consumed_audit(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(7004)
    channel = FakeChannel()
    entry = FakeEntry(8005, "channel_create", guild, actor, channel)
    anti_nuke._SEEN_AUDIT_ENTRY_IDS[entry.id] = time.monotonic()

    async def fake_settings(_guild_id):
        return _settings()

    async def must_not_process(*_args, **_kwargs):
        raise AssertionError("already-consumed audit must not enforce twice")

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", must_not_process)

    asyncio.run(guardian._on_audit_log_entry_create(entry))

    assert channel.deleted is True
    _reset()


def test_cleanup_failure_does_not_suppress_canonical_containment(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(7005)
    channel = FakeChannel(fail=True)
    entry = FakeEntry(8006, "channel_create", guild, actor, channel)
    calls: list[str] = []

    async def fake_settings(_guild_id):
        return _settings()

    async def fake_process(_guild, **kwargs):
        calls.append(kwargs["action_key"])
        return True

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)

    asyncio.run(guardian._on_audit_log_entry_create(entry))

    assert calls == ["channel_create"]
    assert channel.deleted is False
    _reset()
