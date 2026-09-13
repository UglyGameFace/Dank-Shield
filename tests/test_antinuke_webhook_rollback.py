from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_gateway_runtime as gateway


class FakeWebhook:
    def __init__(self, webhook_id: int) -> None:
        self.id = webhook_id
        self.deleted = False
        self.reason = None

    async def delete(self, *, reason=None) -> None:
        self.deleted = True
        self.reason = reason


class FakeGuild:
    def __init__(self, webhooks=None) -> None:
        self.id = 7701
        self.owner_id = 999999
        self._webhooks = list(webhooks or [])

    async def webhooks(self):
        return list(self._webhooks)


def _actor(user_id: int = 8801):
    return SimpleNamespace(id=user_id, roles=[], mention=f"<@{user_id}>")


def _entry(webhook_id: int, actor=None):
    return SimpleNamespace(
        id=9901,
        target=SimpleNamespace(id=webhook_id, name="attacker-hook"),
        user=actor,
    )


def _settings(**patch):
    return anti_nuke.normalize_antinuke_settings(
        {"antinuke_enabled": True, "antinuke_mode": "contain", **patch}
    )


def test_untrusted_created_webhook_is_revoked(monkeypatch) -> None:
    hook = FakeWebhook(12345)
    guild = FakeGuild([hook])
    actor = _actor()

    async def fake_settings(_guild_id):
        return _settings()

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)

    result = asyncio.run(
        gateway._rollback_untrusted_webhook_create(
            guild,
            _entry(hook.id, actor),
            actor,
        )
    )

    assert hook.deleted is True
    assert "deleted unauthorized" in result
    assert "AntiNuke rollback" in str(hook.reason)


def test_webhook_cleanup_matches_exact_audit_target_id(monkeypatch) -> None:
    other = FakeWebhook(11111)
    malicious = FakeWebhook(22222)
    guild = FakeGuild([other, malicious])
    actor = _actor()

    async def fake_settings(_guild_id):
        return _settings()

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)

    asyncio.run(
        gateway._rollback_untrusted_webhook_create(
            guild,
            _entry(malicious.id, actor),
            actor,
        )
    )

    assert other.deleted is False
    assert malicious.deleted is True


def test_delegated_operator_webhook_create_is_not_revoked(monkeypatch) -> None:
    hook = FakeWebhook(33333)
    guild = FakeGuild([hook])
    actor = _actor(8802)

    async def fake_settings(_guild_id):
        return _settings(antinuke_trusted_user_ids=[actor.id])

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)

    result = asyncio.run(
        gateway._rollback_untrusted_webhook_create(
            guild,
            _entry(hook.id, actor),
            actor,
        )
    )

    assert result == ""
    assert hook.deleted is False


def test_alert_mode_webhook_create_is_not_revoked(monkeypatch) -> None:
    hook = FakeWebhook(44444)
    guild = FakeGuild([hook])
    actor = _actor(8803)

    async def fake_settings(_guild_id):
        return anti_nuke.normalize_antinuke_settings(
            {"antinuke_enabled": True, "antinuke_mode": "alert"}
        )

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)

    result = asyncio.run(
        gateway._rollback_untrusted_webhook_create(
            guild,
            _entry(hook.id, actor),
            actor,
        )
    )

    assert result == ""
    assert hook.deleted is False


def test_sparse_gateway_webhook_creation_uses_rest_recovery(monkeypatch) -> None:
    guild = FakeGuild([])
    gateway_entry = SimpleNamespace(
        id=9902,
        guild=guild,
        target=SimpleNamespace(id=55555, name="sparse-hook"),
        user=None,
        user_id=8804,
        action=SimpleNamespace(name="webhook_create"),
    )
    actor = _actor(8804)
    recovered_entry = SimpleNamespace(
        id=9903,
        target=SimpleNamespace(id=55555, name="sparse-hook"),
        user=actor,
    )
    handled: list[int] = []

    async def fake_sleep(_seconds):
        return None

    async def fake_claim(_guild, action_names, *, target_id=None, retries=3):
        assert action_names == ("webhook_create",)
        assert target_id == 55555
        return recovered_entry, actor

    async def fake_handle(_guild, entry, found_actor):
        assert entry is recovered_entry
        assert found_actor is actor
        handled.append(found_actor.id)

    monkeypatch.setattr(gateway.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(gateway.guardian, "_claim_priority_entry", fake_claim)
    monkeypatch.setattr(gateway, "_handle_webhook_create", fake_handle)

    asyncio.run(gateway._recover_webhook_create_from_rest(guild, gateway_entry))

    assert handled == [8804]
